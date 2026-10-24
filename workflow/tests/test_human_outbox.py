from datetime import UTC, datetime, timedelta

import pytest

from workflow.cases import CaseStore
from workflow.human import HumanApprovals, Principal
from workflow.memrails import MemoryRails
from workflow.outbox import Outbox, TestTransport

NOW = datetime(2026, 4, 22, 12, tzinfo=UTC)


def setup():
    rails = MemoryRails.from_world((3,), n_clean=0)
    case = CaseStore(rails).ensure(rails.ledger('keyway')[0], NOW)
    return rails, case


def test_exact_draft_approval_and_delivery_are_durable_and_idempotent():
    rails, case = setup()
    approvals = HumanApprovals(rails)
    payload = {'recipient': 'management:keyway', 'body': 'Please provide the statement.'}
    ticket = approvals.request(case, 'send_message', payload, NOW, 'statement')
    with pytest.raises(PermissionError):
        approvals.decide(ticket['id'], Principal('agent', 'agent'), True, NOW, ticket['digest'])
    approvals.decide(ticket['id'], Principal('controller.dana', 'controller'), True, NOW, ticket['digest'])
    box = Outbox(rails)
    message = box.enqueue(case.id, **payload, now=NOW, key='statement', approval_id=ticket['id'])
    assert box.deliver(message['id'], NOW)['status'] == 'waiting_transport'
    transport = TestTransport()
    receipt = box.deliver(message['id'], NOW, transport)
    assert receipt['status'] == 'delivered_test'
    assert Outbox(MemoryRails(rails.events())).list()[0]['status'] == 'delivered_test'
    box.deliver(message['id'], NOW, transport)
    assert len(transport.deliveries) == 1
    with pytest.raises(ValueError):
        box.enqueue(case.id, recipient=payload['recipient'], body='changed', now=NOW, key='statement', approval_id=ticket['id'])


def test_changed_business_evidence_invalidates_old_approval():
    from contracts.models import Document
    from rails.events.ingest import document_event
    rails, case = setup()
    approvals = HumanApprovals(rails)
    ticket = approvals.request(case, 'case_review', {'reason': 'late'}, NOW, 'review')
    bank = Document(id='new-bank', kind='manager_bank', manager_id='keyway', dated=NOW.date(), data={'payments': [{'invoice_id':case.invoice_id,'vendor_id':case.vendor_id,'amount':1240,'on':'2026-04-22'}]})
    rails.append_event(document_event(bank, NOW))
    with pytest.raises(ValueError, match='stale'):
        approvals.decide(ticket['id'], Principal('controller.dana', 'controller'), True, NOW, ticket['digest'])


def test_expired_and_self_approvals_rejected():
    rails, case = setup()
    approvals = HumanApprovals(rails)
    ticket = approvals.request(case, 'case_review', {}, NOW, 'review', requested_by='controller.dana')
    with pytest.raises(PermissionError):
        approvals.decide(ticket['id'], Principal('controller.dana','controller'),True,NOW,ticket['digest'])
    with pytest.raises(ValueError, match='expired'):
        approvals.decide(ticket['id'], Principal('controller.other','controller'),True,NOW+timedelta(days=8),ticket['digest'])
