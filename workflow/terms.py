"""Read confirmed complete agreement revisions without inventing provenance."""
from __future__ import annotations

from contracts.models import AgreementTerms, SourceKind, Term
from rails.facts import terms_at


def terms_for(rails, agents, manager_id: str, invoice_date=None, vendor_id=None) -> AgreementTerms | None:
    docs = [d for d in rails.documents(manager_id) if d.kind == SourceKind.agreement]
    confirmed = terms_at(rails.events(manager_id), manager_id, invoice_date, vendor_id)
    if confirmed and confirmed.get('terms'):
        terms = AgreementTerms.model_validate(confirmed['terms'])
        updates = {}
        for k in ('vendor_payment_days', 'approval_limit', 'holdback_pct', 'holdback_condition'):
            old = getattr(terms, k)
            if confirmed.get(k) is None:
                updates[k] = None
            elif old is not None:
                updates[k] = old.model_copy(update={'value': confirmed[k]})
        updates.update(allowed_charges=[Term.model_validate(t) for t in confirmed.get('allowed_charges', [])],
                       deadline_basis=confirmed.get('deadline_basis', terms.deadline_basis),
                       effective_from=terms.effective_from, vendor_id=confirmed.get('vendor_id', terms.vendor_id))
        return terms.model_copy(update={**updates, 'confirmed_by_human': True,
                                       'proposal_version': confirmed.get('proposal_version'),
                                       'confirmation_id': confirmed.get('confirmation_id')})
    if not docs:
        return None
    doc = next((d for d in docs if confirmed and d.id == confirmed.get('source_id')), None)
    if confirmed and confirmed.get('source_id') and doc is None:
        return None
    doc = doc or max(docs, key=lambda d: (str(d.dated or ''), d.id))
    # Legacy generated worlds only stored scalar rules: recover their actual clause citations.
    from agents.agreement.rules import extract_terms
    try:
        terms = extract_terms(doc)
    except ValueError:
        terms = agents.extract_terms(doc)
    if confirmed:
        updates = {k: getattr(terms, k).model_copy(update={'value': confirmed[k]})
                   for k in ('vendor_payment_days', 'approval_limit', 'holdback_pct')
                   if getattr(terms, k) is not None and confirmed.get(k) is not None}
        return terms.model_copy(update={**updates, 'confirmed_by_human': True})
    return terms
