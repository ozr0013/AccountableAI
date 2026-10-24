"""Agreement agent (task 2): term sheet with clause citations, awaiting human confirmation.

The LLM reads the whole agreement (wording varies between managers: "ten (10) business days",
"retain 5% pending sign-off"). Guards: every term cites a clause number that exists, quotes it
verbatim, and the quoted text actually contains the value. Output is always
confirmed_by_human=False; the workflow's confirm step is the only way terms become binding.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from contracts.models import AgreementTerms, Citation, Document, SourceKind, Term

from ..guards import citation_problems
from ..llm import StructuredLLM, guarded_call
from . import rules
from .prompts import SYSTEM


class TermOut(BaseModel):
    value: float | int | str
    clause: str = Field(description="clause number, e.g. 7.2")
    quote: str = Field(description="the exact sentence from the clause, copied verbatim")


class AgreementAnswer(BaseModel):
    vendor_payment_days: TermOut
    holdback_pct: TermOut | None = None
    holdback_condition: TermOut | None = None
    approval_limit: TermOut
    allowed_charges: list[TermOut] = Field(default_factory=list)


def _number_in(value, quote: str) -> bool:
    if isinstance(value, str):
        return True
    n = int(value) if float(value).is_integer() else value
    words = {v: k for k, v in rules.WORDS.items()}
    flat = quote.replace(",", "")
    return str(n) in flat or words.get(n, "\0") in quote.lower()


class AgreementAgent:
    def __init__(self, llm: StructuredLLM | None = None):
        self.llm = llm

    def extract_terms(self, agreement: Document) -> AgreementTerms:
        try:
            base: AgreementTerms | None = rules.extract_terms(agreement)
        except ValueError:
            base = None
        numbers = set(rules.clauses(agreement.text))

        def check(a: AgreementAnswer) -> list[str]:
            p = []
            named = {"vendor_payment_days": a.vendor_payment_days, "holdback_pct": a.holdback_pct,
                     "holdback_condition": a.holdback_condition, "approval_limit": a.approval_limit}
            terms = [(k, t) for k, t in named.items() if t] + [("allowed_charge", t)
                                                                for t in a.allowed_charges]
            for name, t in terms:
                if t.clause not in numbers:
                    p.append(f"{name}: clause {t.clause!r} is not in the agreement")
                if not _number_in(t.value, t.quote):
                    p.append(f"{name}: the quote does not contain the value {t.value}")
            p += citation_problems([Citation(source_id=agreement.id, kind=SourceKind.agreement,
                                             quote=t.quote) for _, t in terms], [agreement])
            if not isinstance(a.vendor_payment_days.value, (int, float)) \
                    or not isinstance(a.approval_limit.value, (int, float)):
                p.append("vendor_payment_days and approval_limit must be numbers")
            return p

        ans, problems = guarded_call(self.llm, AgreementAnswer, SYSTEM,
                                     f'<agreement id="{agreement.id}">\n{agreement.text}\n</agreement>',
                                     check)
        if ans is None:
            if base is None:
                raise ValueError(f"could not extract terms from {agreement.id}: {problems}")
            return base

        def term(t: TermOut | None) -> Term | None:
            if t is None:
                return None
            v = t.value
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            return Term(value=v, citation=Citation(source_id=agreement.id, kind=SourceKind.agreement,
                                                   locator=t.clause, quote=re.sub(r"\s+", " ", t.quote)))

        return AgreementTerms(
            manager_id=agreement.manager_id or "", vendor_payment_days=term(ans.vendor_payment_days),
            holdback_pct=term(ans.holdback_pct), holdback_condition=term(ans.holdback_condition),
            approval_limit=term(ans.approval_limit),
            allowed_charges=[term(t) for t in ans.allowed_charges], confirmed_by_human=False)
