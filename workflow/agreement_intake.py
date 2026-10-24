"""Provider-neutral agreement extraction into durable human confirmation proposals."""
from contracts.models import AgreementTerms, Event, LedgerLine, SourceKind
from workflow.cases import CaseStore
from workflow.human import HumanApprovals
from workflow.steps import _term_values


def propose_agreements(rails, cases, now):
    from agents.agreement.agent import AgreementAgent
    # Intake precedes invoices: an agreement can own a human-review case of its own.
    cases = list(cases)
    for doc in rails.documents():
        if doc.kind != SourceKind.agreement or not doc.manager_id or (doc.dated and doc.dated > now.date()):
            continue
        if any(e.type == 'terms_confirmed'
               and (e.payload.get('source_id') == doc.id or (e.source and e.source.source_id == doc.id))
               for e in rails.events(doc.manager_id)):
            continue
        if not any(c.manager_id == doc.manager_id and doc.vendor_id in (None, c.vendor_id) for c in cases):
            case = CaseStore(rails).ensure(LedgerLine(invoice_id='agreement:' + doc.id,
                manager_id=doc.manager_id, vendor_id=doc.vendor_id or '*', amount=0), now, kind='agreement')
            if case.state == 'monitoring':
                case = CaseStore(rails).update(case.id, case.version, now, state='awaiting_terms')
            cases.append(case)
    processed = set()
    for case in cases:
        history = rails.events(case.manager_id)
        for doc in rails.documents(case.manager_id):
            if (doc.kind != SourceKind.agreement or (doc.dated and doc.dated > now.date())
                    or doc.vendor_id not in (None, case.vendor_id)):
                continue
            if doc.id in processed:
                continue
            processed.add(doc.id)
            if any(e.type == 'terms_confirmed' and
                   (e.payload.get('source_id') == doc.id or (e.source and e.source.source_id == doc.id))
                   for e in history):
                continue
            # Parser guards enforce quoted clauses and supported deadline semantics.
            try:
                terms = AgreementAgent().extract_terms(doc)
            except ValueError as exc:
                HumanApprovals(rails).request(case, 'agreement_manual_review',
                    {'source_id': doc.id, 'reason': str(exc)[:1000],
                     'action': 'Provide a clarified agreement; no binding rules were created.'},
                    now, 'agreement-review:' + doc.id)
                continue
            payload = {'source_id': doc.id, 'terms': terms.model_dump(mode='json')}
            HumanApprovals(rails).request(case, 'agreement_terms', payload, now,
                                          'agreement:' + doc.id)


def confirm_agreement(rails, ticket, principal, now):
    terms = AgreementTerms.model_validate(ticket['payload']['terms'])
    source_id = ticket['payload']['source_id']
    existing = next((e for e in rails.events() if e.id == ticket['id'] + ':terms'), None)
    if existing:
        return existing
    values = _term_values(terms)
    event = Event(id=ticket['id'] + ':terms', type='terms_confirmed', manager_id=terms.manager_id,
                  valid_from=now, learned_at=now,
                  payload={**values, 'manager_id': terms.manager_id, 'source_id': source_id,
                           'terms': terms.model_dump(mode='json'), 'confirmed_by': principal.id,
                           'confirmation_id': ticket['id'] + ':terms', 'proposal_version': ticket['id']},
                  source=terms.vendor_payment_days.citation)
    rails.append_event(event)
    case = CaseStore(rails).get(ticket['case_id'])
    if case.kind == 'agreement' and case.state != 'resolved':
        CaseStore(rails).update(case.id, case.version, now, state='resolved',
                               summary='Agreement rules confirmed by ' + principal.id)
    return event
