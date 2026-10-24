"""Verifier: tries to prove each alert wrong using only the evidence.

Order: deterministic rules first (agents/verifier/rules.py), then one model call.
Fails closed: if the model can't run or refuses, the alert is blocked, never approved.
"""

from __future__ import annotations

import json
import os
import time

import anthropic
from pydantic import BaseModel

from contracts.models import AlertDraft, Citation, Document, Verdict, VerdictStatus

from . import rules

DEFAULT_MODEL = "claude-opus-5-5"
# Models that accept the server-side refusal fallback on the Claude API.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"}


SYSTEM = """You are the verifier for AccountableAI. Oakline Homes funds a property manager, \
who must pay vendors from that money. Other agents draft alerts for Oakline's controller. Your \
job is to try to prove each alert WRONG, using only the evidence documents provided. Alerts come \
in several kinds; each states what it asserts and what would refute it. Judge the alert only \
against its own assertion. Defend the manager whenever the evidence supports them.

Source-trust rules:
- Manager bank records beat the manager's monthly statement.
- A vendor confirmation or vendor notice beats a manager's "paid" claim.
- A manager's reply saying "paid" is not proof unless a bank record or vendor confirmation backs it.
- The management agreement is the source of truth for deadlines, holdbacks and approval limits.

Check each claim: is its central assertion supported by the documents it cites? Supporting \
details (an amount, a due date, an invoice number) may come from any evidence document provided. \
A claim is unsupported only if its cited documents do not back its central assertion, or the \
evidence contradicts it.

Rules for your own answer: cite only documents by their exact id. Do not invent facts. \
If the evidence does not refute the alert, say so; do not refute on speculation."""

# What each alert kind asserts, and what evidence would refute it.
KIND_RULES = {
    "unexplained_unpaid": (
        "The manager has not paid a funded vendor invoice past its due date, with no valid reason.",
        "the vendor was in fact paid (bank record or vendor confirmation); the unpaid amount is an "
        "authorized holdback; the payment is not yet due under the agreement; paperwork the vendor "
        "owes is missing; or Oakline never funded or approved it."),
    "paperwork_request": (
        "A vendor is unpaid because paperwork it owes (e.g. a W-9 or complete invoice) is missing; "
        "the recommended action is to request it. This is not an accusation against the manager.",
        "the paperwork was in fact received, or the vendor was paid."),
    "owner_pending_approval": (
        "An invoice is unpaid because Oakline itself has not funded or approved it (e.g. above the "
        "approval limit). This flags Oakline's own pending action, not the manager.",
        "Oakline did fund or approve it, or the amount is within the manager's approval limit."),
    "bank_detail_change": (
        "A vendor asked to change its bank details, so the payment is held until identity is "
        "verified and a controller approves. This is a safety hold, not an accusation.",
        "no such request exists in the evidence."),
    "recurring_pattern": (
        "The manager paid vendors late, beyond the agreement's payment window, in the months listed.",
        "the evidence shows payments in those months were within the agreement's window, or the "
        "agreement allows a longer window than stated."),
    "duplicate_billing": (
        "The same work was billed and funded twice under two invoice numbers.",
        "the two invoices are for different work, periods or amounts, or only one was funded."),
}


class _CounterEvidence(BaseModel):
    source_id: str
    locator: str
    quote: str


class _ClaimCheck(BaseModel):
    index: int
    supported: bool
    why: str


class _Refutation(BaseModel):
    claim_checks: list[_ClaimCheck]
    refuted: bool
    refutation: str
    counter_evidence: list[_CounterEvidence]


def _render_evidence(evidence: list[Document]) -> str:
    out = []
    for d in sorted(evidence, key=lambda d: (str(d.dated or ""), d.id)):
        data = json.dumps(d.data, sort_keys=True) if d.data else "{}"
        out.append(
            f'<document id="{d.id}" kind="{d.kind.value}" dated="{d.dated or ""}">\n'
            f"{d.text}\n<data>{data}</data>\n</document>"
        )
    return "\n".join(out)


def _render_draft(draft: AlertDraft) -> str:
    lines = [f"Alert {draft.id} ({draft.kind.value}) on manager {draft.manager_id}",
             f"Headline: {draft.headline}",
             f"Invoices: {', '.join(draft.invoice_ids)}  Amount: ${draft.amount:,.2f}"]
    for i, c in enumerate(draft.claims):
        cites = ", ".join(f"{x.source_id}{'@' + x.locator if x.locator else ''}" for x in c.citations)
        lines.append(f"Claim {i}: {c.text}  [cites: {cites}]")
    if draft.recommended_action:
        lines.append(f"Recommended action: {draft.recommended_action}")
    return "\n".join(lines)


def _kind_rule(draft: AlertDraft) -> str:
    asserts, refutes = KIND_RULES.get(draft.kind.value, KIND_RULES["unexplained_unpaid"])
    return (f"This {draft.kind.value} alert asserts: {asserts}\n"
            f"It is refuted only if the evidence shows: {refutes}")


class LLMVerifier:
    def __init__(self, client: anthropic.Anthropic | None = None, model: str | None = None):
        self.client = client or anthropic.Anthropic()
        self.model = model or os.getenv("VERIFIER_MODEL", DEFAULT_MODEL)

    def verify(self, draft: AlertDraft, evidence: list[Document]) -> Verdict:
        rule = rules.check(draft, evidence)
        if not rule.ok:
            return Verdict(
                alert_id=draft.id, status=VerdictStatus.blocked, reasons=rule.reasons,
                uncited_claims=rule.uncited_claims, banned_terms=rule.banned_terms,
                meta={"stage": "rules"},
            )

        start = time.monotonic()
        try:
            resp = self._call(draft, evidence)
        except anthropic.APIError as e:
            return self._fail_closed(draft, f"verifier unavailable: {type(e).__name__}")
        meta = {
            "stage": "model",
            "model": resp.model,
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "latency_ms": int((time.monotonic() - start) * 1000),
        }
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            return self._fail_closed(draft, f"verifier gave no answer ({resp.stop_reason})", meta)

        return self._decide(draft, evidence, resp.parsed_output, meta)

    def _call(self, draft: AlertDraft, evidence: list[Document]):
        extra = {}
        if self.model in _FALLBACK_MODELS:
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        return self.client.beta.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{
                "role": "user",
                "content": f"<evidence>\n{_render_evidence(evidence)}\n</evidence>\n\n"
                           f"<alert>\n{_render_draft(draft)}\n</alert>\n\n"
                           f"{_kind_rule(draft)}\n\n"
                           "Try to refute this alert. Check every claim.",
            }],
            output_format=_Refutation,
            **extra,
        )

    def _decide(self, draft, evidence, out: _Refutation, meta) -> Verdict:
        kinds = {d.id: d.kind for d in evidence}
        unsupported = [draft.claims[c.index].text for c in out.claim_checks
                       if not c.supported and 0 <= c.index < len(draft.claims)]
        why_unsupported = [f"claim {c.index} unsupported: {c.why}" for c in out.claim_checks
                           if not c.supported]
        counter = [Citation(source_id=c.source_id, kind=kinds[c.source_id],
                            locator=c.locator or None, quote=c.quote or None)
                   for c in out.counter_evidence if c.source_id in kinds]

        if out.refuted and counter:
            return Verdict(alert_id=draft.id, status=VerdictStatus.refuted,
                           reasons=[out.refutation], counter_evidence=counter, meta=meta)
        if unsupported:
            return Verdict(alert_id=draft.id, status=VerdictStatus.blocked,
                           reasons=why_unsupported, uncited_claims=unsupported, meta=meta)

        reasons = ["survived refutation: " + out.refutation]
        if out.refuted:
            # The verifier holds itself to the same rule: no refutation without a valid source.
            reasons.append("refutation ignored: it cited no known evidence")
        return Verdict(alert_id=draft.id, status=VerdictStatus.approved, reasons=reasons, meta=meta)

    def _fail_closed(self, draft, reason, meta=None) -> Verdict:
        return Verdict(alert_id=draft.id, status=VerdictStatus.blocked,
                       reasons=[reason, "failing closed: alert held for human review"],
                       meta=meta or {"stage": "model"})
