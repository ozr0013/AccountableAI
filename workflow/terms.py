"""Agreement terms for a manager: human-confirmed terms first, else the Agreement agent's draft."""

from __future__ import annotations

from contracts.models import AgreementTerms, Citation, SourceKind, Term
from rails.facts import current_terms

_CLAUSE = {"vendor_payment_days": "7.2", "holdback_pct": "7.4", "approval_limit": "8.1"}


def _agreement_doc(rails, manager_id: str):
    return next((d for d in rails.documents(manager_id) if d.kind == SourceKind.agreement), None)


def terms_for(rails, agents, manager_id: str) -> AgreementTerms | None:
    doc = _agreement_doc(rails, manager_id)
    confirmed = current_terms(rails.events(manager_id)).get(manager_id)
    if confirmed and doc:
        def term(name: str) -> Term | None:
            if confirmed.get(name) is None:
                return None
            return Term(value=confirmed[name], citation=Citation(
                source_id=doc.id, kind=SourceKind.agreement, locator=_CLAUSE[name]))
        return AgreementTerms(
            manager_id=manager_id, vendor_payment_days=term("vendor_payment_days"),
            holdback_pct=term("holdback_pct"), approval_limit=term("approval_limit"),
            confirmed_by_human=True)
    if doc:
        return agents.extract_terms(doc)
    return None
