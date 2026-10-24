"""Decision records: how every agent decision was reached, step by step, streamed and kept.

The workflow wraps its agents and verifier in TracedAgents / TracedVerifier. Each call becomes
one DecisionRecord:
  * steps   the visible reasoning, in order: evidence considered, facts computed, the rule that
            fired, Claude's summarized thinking (when Claude answered), guard checks, fallback,
            and the result. Built from what the agent already computes; nothing is invented.
  * mode    "rules" (no model), "llm" (Claude's answer was accepted), "fallback" (Claude
            answered, the guards rejected it, the rules answered instead)
Records are appended to the event log as `agent_decision` events (the history the 15-day
timeline reads) and pushed to subscribers as they happen (the live stream).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from agents.llm import capture_attempts, stream_thinking
from contracts.models import Citation, Event

log = logging.getLogger(__name__)

Mode = Literal["rules", "llm", "fallback"]


class ReasonStep(BaseModel):
    kind: Literal["evidence", "fact", "rule", "thinking", "model", "guard", "fallback", "result"]
    text: str
    citations: list[Citation] = Field(default_factory=list)


class DecisionRecord(BaseModel):
    id: str
    trigger: str | None                  # the event the workflow was handling
    seq: int                             # order within that run
    at: datetime | None
    day: date | None
    agent: str                           # agreement | trail | cause | chaser | case | verifier
    invoice_id: str | None = None
    question: str
    steps: list[ReasonStep]
    answer: dict
    confidence: float | None = None
    mode: Mode = "rules"
    citations: list[Citation] = Field(default_factory=list)
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    ms: int = 0


_RUN: ContextVar[dict | None] = ContextVar("workflow_run", default=None)


class Tracer:
    """Collects DecisionRecords for the run in progress; persists and broadcasts them."""

    def __init__(self, rails=None):
        self.rails = rails
        self._subs: list[Callable[[DecisionRecord], None]] = []
        self._live: list[Callable[[dict], None]] = []

    @contextmanager
    def run(self, event: Event) -> Iterator[None]:
        token = _RUN.set({"event": event, "seq": 0})
        try:
            yield
        finally:
            _RUN.reset(token)

    def subscribe(self, fn: Callable[[DecisionRecord], None]) -> Callable[[], None]:
        self._subs.append(fn)
        return lambda: self._subs.remove(fn) if fn in self._subs else None

    def subscribe_live(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        """In-progress signals (not kept): a step or agent starting, Claude's thinking pieces."""
        self._live.append(fn)
        return lambda: self._live.remove(fn) if fn in self._live else None

    def live(self, message: dict) -> None:
        if not self._live:
            return
        ctx = _RUN.get()
        msg = {**message, "trigger": ctx["event"].id if ctx else None}
        for fn in list(self._live):
            try:
                fn(msg)
            except Exception:
                log.exception("live subscriber failed")

    def record(self, **fields) -> DecisionRecord:
        ctx = _RUN.get()
        trigger: Event | None = ctx["event"] if ctx else None
        seq = 0
        if ctx is not None:
            ctx["seq"] += 1
            seq = ctx["seq"]
        body = json.dumps({k: fields.get(k) for k in ("agent", "invoice_id", "answer")},
                          sort_keys=True, default=str)
        rid = "dec-" + hashlib.sha1(f"{trigger.id if trigger else ''}|{seq}|{body}".encode()).hexdigest()[:12]
        rec = DecisionRecord(id=rid, trigger=trigger.id if trigger else None, seq=seq,
                             at=trigger.learned_at if trigger else None,
                             day=trigger.learned_at.date() if trigger else None, **fields)
        if self.rails is not None and trigger is not None:
            self.rails.append_event(Event(
                id=f"ev-wf-agent_decision-{rid}", type="agent_decision",
                manager_id=trigger.manager_id, invoice_id=rec.invoice_id,
                valid_from=trigger.learned_at, learned_at=trigger.learned_at,
                payload={**rec.model_dump(mode="json"), "trigger": trigger.id}))
        for fn in list(self._subs):
            try:
                fn(rec)
            except Exception:
                log.exception("decision subscriber failed")
        return rec


# -- turning the model attempts into steps -------------------------------------------------

def _model_steps(attempts: list[dict]) -> tuple[list[ReasonStep], Mode, dict]:
    steps: list[ReasonStep] = []
    tried = [a for a in attempts if a.get("kind") == "attempt"]
    for a in tried:
        label = f"attempt {a['attempt']}" if len(tried) > 1 else "Claude"
        if a.get("thinking"):
            steps.append(ReasonStep(kind="thinking", text=a["thinking"]))
        if a.get("error"):
            steps.append(ReasonStep(kind="model", text=f"{label}: call failed ({a['error']})"))
        elif a.get("answer"):
            steps.append(ReasonStep(kind="model", text=f"{label} answered: {a['answer']}"))
        if a.get("accepted"):
            steps.append(ReasonStep(kind="guard", text="Guards passed: real citations, consistent "
                                                     "with the hard facts, neutral wording."))
        elif a.get("problems") and a["problems"] != ["no answer"]:
            steps.append(ReasonStep(kind="guard", text="Rejected: " + "; ".join(a["problems"])))
    mode: Mode = "rules"
    if tried:
        mode = "llm" if any(a.get("accepted") for a in tried) else "fallback"
        if mode == "fallback":
            steps.append(ReasonStep(kind="fallback", text="Claude's answer didn't pass the guards; "
                                                        "the rules engine decided instead."))
    usage = {"model": next((a.get("model") for a in reversed(tried) if a.get("model")), None),
             "tokens_in": sum(a.get("input_tokens") or 0 for a in tried) or None,
             "tokens_out": sum(a.get("output_tokens") or 0 for a in tried) or None}
    return steps, mode, usage


def _cite(c: Citation | None) -> list[Citation]:
    return [c] if c else []


class TracedAgents:
    """contracts.steps.Agents with a DecisionRecord per call. Everything else passes through."""

    def __init__(self, inner, tracer: Tracer):
        self.inner, self.tracer = inner, tracer

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def _call(self, agent: str, invoice_id: str | None, question: str, fn, *args):
        start = time.monotonic()
        self.tracer.live({"type": "agent_started", "agent": agent, "invoice_id": invoice_id,
                          "question": question})

        def thinking(piece: str) -> None:
            self.tracer.live({"type": "thinking", "agent": agent, "invoice_id": invoice_id,
                              "text": piece})

        with capture_attempts() as attempts, stream_thinking(thinking):
            out = fn(*args)
        model_steps, mode, usage = _model_steps(attempts)
        return out, model_steps, mode, usage, int((time.monotonic() - start) * 1000)

    def extract_terms(self, agreement):
        q = f"What does {agreement.id} require?"
        t, ms_steps, mode, usage, ms = self._call("agreement", None, q, self.inner.extract_terms, agreement)
        terms = [(n, getattr(t, n)) for n in ("vendor_payment_days", "holdback_pct",
                                              "holdback_condition", "approval_limit") if getattr(t, n)]
        steps = [ReasonStep(kind="evidence", text=f"Clause {v.citation.locator}: “{v.citation.quote}”",
                            citations=[v.citation]) for _, v in terms]
        steps += ms_steps + [ReasonStep(kind="result", text="Proposed terms (a controller must confirm): "
                                        + ", ".join(f"{n}={v.value}" for n, v in terms))]
        self.tracer.record(agent="agreement", question=q,
                           steps=steps, answer={n: v.value for n, v in terms}, mode=mode,
                           citations=[v.citation for _, v in terms], ms=ms, **usage)
        return t

    def build_trail(self, line, docs):
        q = f"What really happened to the payment for {line.invoice_id}?"
        trail, ms_steps, mode, usage, ms = self._call("trail", line.invoice_id, q,
                                                      self.inner.build_trail, line, docs)
        steps = [ReasonStep(kind="evidence", text=f"{e.on or 'undated'} · {e.claim}",
                            citations=[e.citation]) for e in trail.entries]
        steps.append(ReasonStep(kind="rule", text="Source-trust rules: bank record > vendor "
                                "confirmation or notice > manager statement or reply. " + trail.reasoning,
                                citations=trail.winning_sources))
        steps += ms_steps
        steps.append(ReasonStep(kind="result", text=f"Winner: {trail.winning_claim}"
                                + (" (sources disagree)" if trail.conflict else "")))
        self.tracer.record(agent="trail", invoice_id=line.invoice_id,
                           question=q,
                           steps=steps, answer={"winning_claim": trail.winning_claim,
                                                "conflict": trail.conflict},
                           mode=mode, citations=trail.winning_sources, ms=ms, **usage)
        return trail

    def classify_cause(self, line, trail, terms):
        from agents.cause.rules import hard_facts
        q = f"Why is {line.invoice_id} delayed, and whose side is it on?"
        f, ms_steps, mode, usage, ms = self._call("cause", line.invoice_id, q,
                                                  self.inner.classify_cause, line, trail, terms)
        facts = hard_facts(line, trail, terms)
        days = terms.vendor_payment_days
        steps = [
            ReasonStep(kind="fact", text=f"Funded by Oakline: {'yes' if facts['funded'] else 'no'} "
                                         f"(${line.funded_amount:,.2f} of ${line.amount:,.2f})"),
            ReasonStep(kind="fact", text=f"Due {line.due_date}: {days.value} days from receipt "
                                         f"{line.invoice_received}", citations=_cite(days.citation)),
            ReasonStep(kind="fact", text=f"Still unpaid: ${facts['unpaid_amount']:,.2f}; paid on time: "
                                         f"{'yes' if facts['paid_on_time'] else 'no'}"),
            ReasonStep(kind="fact", text=f"Within the agreement's holdback: "
                                         f"{'yes' if facts['within_holdback'] else 'no'}; inspection "
                                         f"pending: {'yes' if facts['inspection_pending'] else 'no'}"),
            ReasonStep(kind="fact", text=f"Payment trail says: {facts['trust_rules_winner']}"),
        ]
        steps += ms_steps
        steps.append(ReasonStep(kind="result", text=f"Cause: {f.cause.value.replace('_', ' ')}. "
                                f"{f.explanation}", citations=f.citations))
        self.tracer.record(agent="cause", invoice_id=line.invoice_id,
                           question=q,
                           steps=steps, answer={"cause": f.cause.value}, confidence=f.confidence,
                           mode=mode, citations=f.citations, ms=ms, **usage)
        return f

    def draft_chase(self, manager_id, alerts, prior):
        q = f"Which reminder should {manager_id} get next?"
        invoices = sorted({i for a in alerts for i in a.draft.invoice_ids})
        msg, ms_steps, mode, usage, ms = self._call("chaser", (invoices or [None])[0], q,
                                                    self.inner.draft_chase, manager_id, alerts, prior)
        sent = [p.level for p in prior if p.sent_at]
        steps = [ReasonStep(kind="fact", text=f"Earlier reminders for {', '.join(msg.invoice_ids)}: "
                                              f"{', '.join(sent) or 'none'}"),
                 ReasonStep(kind="rule", text="Approved schedule: polite → firm → escalation, "
                                              "controller-approved templates only."),
                 *ms_steps,
                 ReasonStep(kind="result", text=f"Next: {msg.level} reminder ({msg.template_id})")]
        self.tracer.record(agent="chaser", invoice_id=(msg.invoice_ids or [None])[0],
                           question=q, steps=steps,
                           answer={"level": msg.level, "template_id": msg.template_id,
                                   "invoice_ids": msg.invoice_ids}, mode=mode, ms=ms, **usage)
        return msg

    def read_reply(self, reply_text, manager_id, docs):
        q = "Does the manager's reply prove payment?"
        r, ms_steps, mode, usage, ms = self._call("chaser", None, q, self.inner.read_reply,
                                                  reply_text, manager_id, docs)
        steps = [ReasonStep(kind="evidence", text=f"{manager_id} wrote: “{reply_text}”"),
                 ReasonStep(kind="rule", text="A reply is never proof by itself. Only a bank record "
                                              "or the vendor's confirmation showing the payment counts."),
                 *ms_steps,
                 ReasonStep(kind="result", text=(
                     "Claims payment, backed by " + ", ".join(c.source_id for c in r.proof)
                     if r.claims_paid and r.proof else
                     "Claims payment with no vendor or bank proof: holding position"
                     if r.claims_paid else "Does not claim payment"), citations=r.proof)]
        self.tracer.record(agent="chaser", invoice_id=(r.invoice_ids or [None])[0],
                           question=q, steps=steps,
                           answer={"claims_paid": r.claims_paid, "proof": [c.source_id for c in r.proof]},
                           mode=mode, citations=r.proof, ms=ms, **usage)
        return r

    def build_case(self, manager_id, alerts):
        q = f"How much of the next funding to {manager_id} should be held?"
        case, ms_steps, mode, usage, ms = self._call("case", None, q, self.inner.build_case,
                                                     manager_id, alerts)
        held = [a for a in alerts if a.draft.id in case.alerts]
        steps = [ReasonStep(kind="fact", text=f"{a.draft.invoice_ids[0]}: ${a.draft.amount:,.2f} "
                                              "funded and still unpaid, unexplained",
                            citations=[c for cl in a.draft.claims for c in cl.citations][:3])
                 for a in held]
        steps.append(ReasonStep(kind="rule", text="Hold = the sum still owed on open, verified, "
                                                  "unexplained items. Agents only recommend."))
        steps += ms_steps
        steps.append(ReasonStep(kind="result", text=case.summary))
        self.tracer.record(agent="case", question=q, steps=steps,
                           answer={"proposed_hold": case.proposed_hold, "alerts": case.alerts},
                           mode=mode, citations=case.evidence, ms=ms, **usage)
        return case


class TracedVerifier:
    """contracts.steps.Verifier with a DecisionRecord per verdict."""

    def __init__(self, inner, tracer: Tracer):
        self.inner, self.tracer = inner, tracer

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def verify(self, draft, evidence):
        start = time.monotonic()
        q = f"Does the alert “{draft.headline}” survive scrutiny?"
        self.tracer.live({"type": "agent_started", "agent": "verifier",
                          "invoice_id": (draft.invoice_ids or [None])[0], "question": q})
        v = self.inner.verify(draft, evidence)
        meta = v.meta or {}
        steps = [ReasonStep(kind="evidence", text=f"Claim: {c.text}", citations=c.citations)
                 for c in draft.claims]
        steps.append(ReasonStep(kind="rule", text="Try to prove the alert wrong from the cited "
                                                  "evidence only; block uncited or accusatory claims."))
        steps += [ReasonStep(kind="guard", text=r) for r in v.reasons]
        steps.append(ReasonStep(kind="result", text=f"Verdict: {v.status.value}",
                                citations=v.counter_evidence))
        self.tracer.record(agent="verifier", invoice_id=(draft.invoice_ids or [None])[0],
                           question=q,
                           steps=steps, answer={"alert_id": draft.id, "status": v.status.value},
                           mode="llm" if meta.get("stage") == "model" else "rules",
                           model=meta.get("model"), tokens_in=meta.get("input_tokens"),
                           tokens_out=meta.get("output_tokens"),
                           ms=meta.get("latency_ms") or int((time.monotonic() - start) * 1000))
        return v
