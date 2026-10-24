"""Delay-cause agent (task 4): one of five causes, with evidence and confidence.

The LLM reads the trail (including free-text reasons such as "ON HOLD, vendor W-9 not on file")
and judges. Guards hold it to the hard facts: unfunded can only be owner-side, paid on time can
never be unexplained, a holdback must fit the agreement's percentage, and citations must point
at sources in the trail or the agreement. Rejected twice -> the rules answer.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from contracts.models import AgreementTerms, Cause, CauseFinding, Citation, LedgerLine, PaymentTrail

from ..guards import wording_problems
from ..llm import StructuredLLM, guarded_call
from . import rules
from .prompts import SYSTEM


class _Cite(BaseModel):
    source_id: str
    locator: str | None = None


class CauseAnswer(BaseModel):
    cause: Cause
    confidence: float = Field(ge=0, le=1)
    explanation: str = Field(description="2-3 neutral sentences a controller can act on")
    citations: list[_Cite] = Field(description="sources that back the explanation")


def _allowed(trail: PaymentTrail, terms: AgreementTerms) -> dict[str, Citation]:
    out = {e.citation.source_id: e.citation for e in trail.entries}
    for t in (terms.vendor_payment_days, terms.holdback_pct, terms.holdback_condition,
              terms.approval_limit, *terms.allowed_charges):
        if t:
            out.setdefault(t.citation.source_id, t.citation)
    return out


def fact_problems(ans: CauseAnswer, facts: dict) -> list[str]:
    p = []
    if not facts["funded"] and ans.cause != Cause.owner_side:
        p.append("Oakline never funded this invoice, so the cause must be owner_side")
    if facts["funded"] and ans.cause == Cause.owner_side:
        p.append("Oakline funded this invoice, so it cannot be owner_side")
    if facts.get("bank_change_pending") and ans.cause == Cause.unexplained:
        p.append("payment is held for the vendor's bank-detail change; it is not unexplained")
    if facts["paid_on_time"] and ans.cause == Cause.unexplained:
        p.append("the invoice was paid on time under the agreement; it is not unexplained")
    if ans.cause == Cause.unexplained and facts["within_holdback"] and facts["inspection_pending"]:
        p.append("the unpaid amount is within the agreement's holdback while inspection is "
                 "pending; it is an authorized hold, not unexplained")
    if ans.cause == Cause.unexplained and facts.get("paperwork_hold"):
        p.append("the manager's statement shows this payment on hold for missing vendor "
                 "paperwork; it is paperwork pending, not unexplained")
    if ans.cause == Cause.authorized_hold and not facts["within_holdback"]:
        p.append("the unpaid amount is larger than the agreement's holdback allows")
    if ans.cause == Cause.unexplained and facts["trust_rules_winner"] == "paid" \
            and not facts["paid_on_time"] and facts["unpaid_amount"] <= 0:
        p.append("unexplained needs an unpaid amount or a late payment")
    return p


class CauseAgent:
    def __init__(self, llm: StructuredLLM | None = None):
        self.llm = llm

    def classify_cause(self, line: LedgerLine, trail: PaymentTrail,
                       terms: AgreementTerms) -> CauseFinding:
        base = rules.classify(line, trail, terms)
        facts = rules.hard_facts(line, trail, terms)
        allowed = _allowed(trail, terms)

        def check(ans: CauseAnswer) -> list[str]:
            p = fact_problems(ans, facts)
            p += [f"cites unknown source {c.source_id!r}" for c in ans.citations
                  if c.source_id not in allowed]
            if not ans.citations:
                p.append("at least one citation is required")
            return p + wording_problems(ans.explanation)

        prompt = self._prompt(line, trail, terms, facts, base)
        ans, _ = guarded_call(self.llm, CauseAnswer, SYSTEM, prompt, check)
        if ans is None:
            return base
        cites = [allowed[c.source_id].model_copy(update={"locator": c.locator or
                                                         allowed[c.source_id].locator})
                 for c in ans.citations]
        return CauseFinding(invoice_id=line.invoice_id, cause=ans.cause,
                            confidence=ans.confidence, explanation=ans.explanation,
                            citations=list({c.source_id: c for c in cites}.values()))

    @staticmethod
    def _prompt(line, trail, terms, facts, base: CauseFinding) -> str:
        data = {
            "invoice": line.model_dump(mode="json"),
            "agreement_terms": {k: (v.model_dump(mode="json") if v else None) for k, v in {
                "vendor_payment_days": terms.vendor_payment_days,
                "holdback_pct": terms.holdback_pct,
                "holdback_condition": terms.holdback_condition,
                "approval_limit": terms.approval_limit}.items()},
            "hard_facts": facts,
            "payment_trail": trail.model_dump(mode="json"),
            "rules_engine_suggestion": {"cause": base.cause.value,
                                        "explanation": base.explanation},
        }
        return (f"<case>\n{json.dumps(data, indent=1, default=str)}\n</case>\n\n"
                f"Classify why {line.invoice_id} is delayed.")
