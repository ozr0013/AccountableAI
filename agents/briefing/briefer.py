"""Briefing agent: "What's open with Keyway?" answered from the workflow's own record, cited.

Two layers, same output (contracts.models.Briefing):
  * code briefing: built straight from open alerts, past rulings and reminders. Free, always
    available, every claim carries the alert's own citations. Also the fallback.
  * model briefing: one structured Claude call that writes a short answer over the same record.
    Code checks every claim: it must cite documents the model was shown, use no accusatory
    wording, and the answer itself must pass the wording check. Claims that fail are dropped;
    if the answer fails, or the call fails, the code briefing is returned instead.
"""

from __future__ import annotations

import logging
import os

import anthropic
from pydantic import BaseModel

from contracts.models import Alert, AlertKind, Briefing, Citation, Claim, Document
from contracts.steps import Rails

from ..verifier.rules import find_banned

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5-5"     # README: Sonnet for the briefing agent
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"}

SYSTEM = """You brief Oakline Homes' controller on a property manager that Oakline funds to pay \
vendors. You are given the system's current record: open alerts (each with its cited source \
documents), past rulings (alerts that were withdrawn, with the reason), reminders sent to the \
manager, the funded-versus-paid reconciliation, and the source documents themselves.

Answer the controller's question using only that record.
- Every claim must cite one or more source documents by exact id, taken from the documents given.
- Lead with what needs the controller's attention: unexplained unpaid vendor money and its total.
- Separate items that are NOT the manager's fault (Oakline's own pending approval, missing vendor \
paperwork, a safety hold on a bank-detail change) from items that are.
- Mention relevant past rulings, e.g. an alert withdrawn after a corrected fact.
- Neutral wording only. Never accuse: no "fraud", "misuse", "theft" or similar. The strongest \
word for an unexplained item is "unexplained".
- The answer is 2-5 short sentences and states no fact that isn't in your claims."""

_NOT_MANAGER = {AlertKind.owner_pending_approval: "Oakline's own approval or funding is pending",
                AlertKind.paperwork_request: "waiting on vendor paperwork",
                AlertKind.bank_detail_change: "payment held for a vendor bank-detail check"}


class _ClaimOut(BaseModel):
    text: str
    source_ids: list[str]


class _BriefingOut(BaseModel):
    answer: str
    claims: list[_ClaimOut]
    prior_rulings: list[str]


def _cites(alert: Alert) -> list[Citation]:
    seen: dict[str, Citation] = {}
    for cl in alert.draft.claims:
        for c in cl.citations:
            seen.setdefault(c.source_id, c)
    return list(seen.values())


class Record:
    """Everything the briefing may say, for one manager."""

    def __init__(self, rails: Rails, manager_id: str):
        from workflow.book import WorkflowBook
        book = WorkflowBook.load(rails)
        self.manager_id = manager_id
        self.open = book.open_alerts(manager_id)
        self.withdrawn = sorted((a for a in book.alerts.values()
                                 if a.draft.manager_id == manager_id and a.state == "withdrawn"),
                                key=lambda a: a.draft.id)
        self.chases = book.chases.get(manager_id, [])
        self.recon = rails.reconcile(manager_id)
        cited = {c.source_id for a in self.open + self.withdrawn for c in _cites(a)}
        self.docs: list[Document] = [d for d in rails.documents(manager_id) if d.id in cited]

    @property
    def unexplained(self) -> list[Alert]:
        return [a for a in self.open if a.draft.kind == AlertKind.unexplained_unpaid and a.draft.amount > 0]

    def render(self) -> str:
        def alert_line(a: Alert) -> str:
            ids = ", ".join(c.source_id for c in _cites(a))
            return f"- [{a.draft.kind.value}] {a.draft.headline} (${a.draft.amount:,.2f}) [sources: {ids}]"
        r = self.recon
        alerts = [alert_line(a) for a in self.open] or ["- none"]
        rulings = [f"- {a.draft.headline}: withdrawn because {a.withdrawn_reason}"
                   for a in self.withdrawn] or ["- none"]
        chases = [f"- {c.sent_at.date() if c.sent_at else '?'} {c.level}: {', '.join(c.invoice_ids)}"
                  for c in self.chases] or ["- none"]
        parts = [f"Manager: {self.manager_id}",
                 f"Reconciliation: funded ${r['funded']:,.2f}, paid to vendors ${r['paid']:,.2f}, "
                 f"held ${r['held']:,.2f}, still owed ${r['owed']:,.2f}",
                 "", "Open alerts:", *alerts,
                 "", "Past rulings (withdrawn alerts):", *rulings,
                 "", "Reminders sent:", *chases,
                 "", "Source documents:"]
        for d in sorted(self.docs, key=lambda d: (str(d.dated or ""), d.id)):
            parts.append(f'<document id="{d.id}" kind="{d.kind.value}" dated="{d.dated or ""}">'
                         f"{d.text}</document>")
        return "\n".join(parts)


def code_briefing(rec: Record, question: str) -> Briefing:
    """Deterministic briefing: one cited claim per open alert, past rulings verbatim."""
    unexplained = rec.unexplained
    total = sum(a.draft.amount for a in unexplained)
    other = [a for a in rec.open if a.draft.kind in _NOT_MANAGER]
    pattern = [a for a in rec.open if a.draft.kind == AlertKind.recurring_pattern]
    dup = [a for a in rec.open if a.draft.kind == AlertKind.duplicate_billing]
    sentences = [f"{rec.manager_id.title()} has {len(unexplained)} unexplained unpaid vendor "
                 f"item(s) totaling ${total:,.2f}."]
    if pattern:
        sentences.append(pattern[0].draft.headline.rstrip(".") + ".")
    if dup:
        sentences.append(f"{len(dup)} duplicate billing item(s) flagged.")
    if other:
        sentences.append(f"{len(other)} open item(s) are not the manager's fault: "
                         + "; ".join(f"{a.draft.invoice_ids[0] if a.draft.invoice_ids else ''} "
                                     f"({_NOT_MANAGER[a.draft.kind]})" for a in other) + ".")
    claims = [Claim(text=a.draft.headline, citations=_cites(a)) for a in rec.open]
    rulings = [f"{a.draft.headline} -> withdrawn: {a.withdrawn_reason}" for a in rec.withdrawn]
    return Briefing(manager_id=rec.manager_id, question=question, answer=" ".join(sentences),
                    claims=claims, prior_rulings=rulings)


class Briefer:
    """contracts.steps.Briefer. Uses the model when a client is available, else the code briefing."""

    def __init__(self, rails: Rails, client: anthropic.Anthropic | None = None,
                 model: str | None = None, use_model: bool = True):
        self.rails = rails
        self.model = model or os.getenv("BRIEFING_MODEL", DEFAULT_MODEL)
        self.client = client
        if client is None and use_model and (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
            self.client = anthropic.Anthropic()
        self.last: dict = {}             # how the last briefing was made, for logs and evals

    def brief(self, manager_id: str, question: str) -> Briefing:
        rec = Record(self.rails, manager_id)
        fallback = code_briefing(rec, question)
        if self.client is None:
            self.last = {"mode": "code"}
            return fallback
        try:
            resp = self._call(rec, question)
        except anthropic.APIError as e:
            log.warning("briefing model call failed: %s", e)
            self.last = {"mode": "code", "why": f"model unavailable: {type(e).__name__}"}
            return fallback
        out = resp.parsed_output
        if resp.stop_reason == "refusal" or out is None:
            self.last = {"mode": "code", "why": f"no model answer ({resp.stop_reason})"}
            return fallback
        return self._check(rec, question, out, fallback, resp)

    def _call(self, rec: Record, question: str):
        extra = {}
        if self.model in _FALLBACK_MODELS:
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        return self.client.beta.messages.parse(
            model=self.model, max_tokens=16000, system=SYSTEM,
            messages=[{"role": "user", "content": f"<record>\n{rec.render()}\n</record>\n\n"
                                                  f"Controller's question: {question}"}],
            output_format=_BriefingOut, **extra)

    def _check(self, rec: Record, question: str, out: _BriefingOut, fallback: Briefing, resp) -> Briefing:
        kinds = {d.id: d.kind for d in rec.docs}
        claims, dropped = [], []
        for c in out.claims:
            ids = [i for i in c.source_ids if i in kinds]
            if not ids or len(ids) < len(c.source_ids) or find_banned(c.text):
                dropped.append(c.text)
                continue
            claims.append(Claim(text=c.text, citations=[Citation(source_id=i, kind=kinds[i]) for i in ids]))
        meta = {"model": resp.model, "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens, "dropped_claims": dropped}
        if find_banned(out.answer) or not claims:
            self.last = {"mode": "code", "why": "model answer failed the checks", **meta}
            return fallback
        self.last = {"mode": "model", **meta}
        rulings = out.prior_rulings or fallback.prior_rulings
        return Briefing(manager_id=rec.manager_id, question=question, answer=out.answer,
                        claims=claims, prior_rulings=rulings)
