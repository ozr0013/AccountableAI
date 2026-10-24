"""Deterministic checks. Run before any model call: free, instant, and never wrong about format."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from contracts.models import AlertDraft, Document
from contracts.policy import BANNED_TERMS


@dataclass
class RuleResult:
    uncited_claims: list[str] = field(default_factory=list)
    banned_terms: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.uncited_claims or self.banned_terms or self.reasons)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def find_banned(text: str) -> list[str]:
    low = text.lower()
    return [t for t in BANNED_TERMS if re.search(rf"\b{re.escape(t)}", low)]


def check(draft: AlertDraft, evidence: list[Document]) -> RuleResult:
    docs = {d.id: d for d in evidence}
    r = RuleResult()

    if not draft.claims:
        r.reasons.append("alert has no claims")

    for claim in draft.claims:
        if not claim.citations:
            r.uncited_claims.append(claim.text)
            r.reasons.append(f"no citation: {claim.text!r}")
            continue
        for c in claim.citations:
            doc = docs.get(c.source_id)
            if doc is None:
                r.uncited_claims.append(claim.text)
                r.reasons.append(f"cites unknown source {c.source_id}")
                break
            if c.quote and _norm(c.quote) not in _norm(doc.text):
                r.uncited_claims.append(claim.text)
                r.reasons.append(f"quote not found in {c.source_id}: {c.quote!r}")
                break

    text = " ".join([draft.headline, draft.recommended_action or "", *(c.text for c in draft.claims)])
    r.banned_terms = find_banned(text)
    if r.banned_terms:
        r.reasons.append(f"accusatory wording: {', '.join(r.banned_terms)}")
    return r
