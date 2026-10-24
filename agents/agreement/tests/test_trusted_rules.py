from datetime import date

import pytest

from agents.agreement.agent import _number_in
from agents.agreement.rules import extract_terms
from contracts.models import Document, Event, SourceKind
from rails.events.ingest import document_event
from rails.facts import terms_at
from rails.guards.payments import payment_proof
from rails.ledger.build import build_ledger


def agreement(text):
    return Document(id='agreement', kind=SourceKind.agreement, manager_id='m', text=text)


def test_numeric_guard_requires_whole_token():
    assert not _number_in(1, 'within ten (10) days')
    assert not _number_in(500, 'limit $5,000')
    assert _number_in(5000, 'limit $5,000')


def test_business_basis_and_unsupported_trigger():
    t = extract_terms(agreement('4.1 Pay vendors within ten (10) business days of invoice receipt.\n6.2 Owner approval above $500.'))
    assert t.deadline_basis == 'business'
    with pytest.raises(ValueError, match='deadline'):
        extract_terms(agreement('4.1 Pay vendors within ten (10) days after owner signs off.\n6.2 Owner approval above $500.'))


def events():
    invoice = Document(id='invdoc', kind=SourceKind.vendor_invoice, manager_id='m', vendor_id='v', dated=date(2026,4,3),
                       data={'invoice_id':'INV-1','amount':1000,'received':'2026-04-03'})
    base = document_event(invoice)
    terms = Event(id='terms', type='terms_confirmed', manager_id='m', valid_from=base.valid_from,
                  learned_at=base.learned_at, payload={'vendor_payment_days':2,'deadline_basis':'business'})
    return base, terms


def test_business_deadline_and_future_amendment():
    inv, terms = events()
    future = terms.model_copy(update={'id':'future','payload':{'vendor_payment_days':60, 'effective_from':'2027-01-01'}})
    assert build_ledger([inv,terms,future])[0].due_date == date(2026,4,7)
    assert terms_at([terms,future], 'm', date(2026,4,3))['vendor_payment_days'] == 2


def test_payment_proof_needs_identity_and_full_amount():
    inv, _ = events()
    invoice = Document.model_validate(inv.payload)
    bank = Document(id='bank',kind=SourceKind.manager_bank,manager_id='m',dated=date(2026,4,4),data={'payments':[{'invoice_id':'INV-1','amount':1000}]})
    assert not payment_proof(['INV-1'],[invoice,bank])
    assert build_ledger([inv,document_event(bank)])[0].paid_amount == 0
    bank.data['payments'][0]['vendor_id'] = 'v'
    bank.data['payments'][0]['amount'] = 900
    assert not payment_proof(['INV-1'],[invoice,bank])


def test_unknown_holdback_condition_does_not_authorize_inspection_hold():
    from rails.guards.payments import payable_cents
    terms = {'holdback_pct':10, 'holdback_condition':'until the owner signs off', 'terms':{}}
    assert payable_cents(1000, terms, {'inspection':'pending'}) == 100000
    terms['holdback_condition'] = 'until the work passes inspection'
    assert payable_cents(1000, terms, {'inspection':'pending'}) == 90000


def test_calendar_weekend_deadline_is_not_rolled():
    inv, terms = events()
    terms.payload['deadline_basis'] = 'calendar'
    assert build_ledger([inv, terms])[0].due_date == date(2026,4,5)


def test_clause_quote_must_match_cited_clause():
    from agents.agreement.agent import AgreementAgent, AgreementAnswer
    from agents.testing import ScriptedLLM
    d = agreement('4.1 Pay vendors within ten (10) days of invoice receipt.\n6.2 Owner approval above $500.')
    answer = AgreementAnswer(vendor_payment_days={'value':1,'clause':'6.2',
        'quote':'Pay vendors within ten (10) days of invoice receipt.'},
        approval_limit={'value':500,'clause':'6.2','quote':'Owner approval above $500.'})
    llm = ScriptedLLM(answer, answer)
    terms = AgreementAgent(llm).extract_terms(d)
    assert terms.vendor_payment_days.value == 10
    assert 'does not belong to cited clause' in llm.prompts[1]


def test_same_effective_date_revisions_use_append_order_and_vendor_scope():
    _, old = events()
    old.id = 'z-old'
    newest = old.model_copy(update={'id':'a-new','payload':{'vendor_payment_days':20}})
    scoped = old.model_copy(update={'id':'vendor-only','payload':{'vendor_payment_days':30,'vendor_id':'other'}})
    assert terms_at([old,newest,scoped], 'm', date(2026,4,3), 'v')['vendor_payment_days'] == 20
    assert terms_at([old,newest,scoped], 'm', date(2026,4,3), 'other')['vendor_payment_days'] == 30
