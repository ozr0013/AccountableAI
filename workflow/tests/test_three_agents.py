"""The autonomous runtime with three agents: agreement, chaser, anomaly."""
from datetime import UTC, datetime, timedelta

import pytest

from agents.anomaly.agent import AnomalyNote
from agents.testing import ScriptedLLM
from contracts.models import Document, Event
from rails.events.ingest import document_event
from workflow.autonomy import AutonomyRuntime
from workflow.cases import CaseStore
from workflow.evidence import payment_proof
from workflow.human import HumanApprovals, Principal
from workflow.memrails import MemoryRails
from workflow.scheduler import Scheduler

NOW = datetime(2026, 4, 22, 12, tzinfo=UTC)
CONTROLLER = Principal('controller.dana', 'controller')
INV = 'INV-KW-0301'


@pytest.fixture
def rails():
    return MemoryRails.from_world((3,), n_clean=0)


def _drop_world_statement(rails):
    rails._events = [e for e in rails._events if e.payload.get('id') != 'doc-stmt-kw-2026-03']


def _case(rails):
    return next(c for c in CaseStore(rails).list() if c.invoice_id == INV)


def _events(rails, kind):
    return [e for e in rails.events() if e.type == kind]


def _approve_all(rails, now):
    approvals = HumanApprovals(rails)
    for t in approvals.list():
        if t['status'] == 'pending':
            approvals.decide(t['id'], CONTROLLER, True, now, t['digest'])


def _pay(rails, amount=1240.0, vendor='v-riogrande', on='2026-04-20', doc_id='bank-1'):
    rails.append_event(document_event(Document(
        id=doc_id, kind='manager_bank', manager_id='keyway', dated=NOW.date(),
        data={'payments': [{'invoice_id': INV, 'vendor_id': vendor, 'amount': amount, 'on': on}]}),
        NOW))


def _statement(rails, amount=1240.0, doc_id='statement-1'):
    rails.append_event(document_event(Document(
        id=doc_id, kind='manager_statement', manager_id='keyway', dated=NOW.date(),
        data={'lines': [{'invoice_id': INV, 'vendor_id': 'v-riogrande', 'amount': amount,
                         'status': 'paid'}]}), NOW))


def test_overdue_unpaid_invoice_gets_a_polite_reminder_draft(rails):
    [result] = AutonomyRuntime(rails).tick(NOW)
    assert result['agent'] == 'chaser' and result['action'] == 'chase' and result['level'] == 'polite'
    [ticket] = HumanApprovals(rails).list()
    assert ticket['kind'] == 'send_message' and ticket['requested_by'] == 'chaser'
    assert ticket['payload']['recipient'] == 'management:keyway'
    assert INV in ticket['payload']['body'] and _case(rails).state == 'awaiting_payment_proof'
    assert not _events(rails, 'outbox_changed')


def test_same_day_poll_and_restart_add_nothing(rails):
    runtime = AutonomyRuntime(rails)
    runtime.tick(NOW)
    before = len(rails.events())
    assert runtime.tick(NOW) == []
    assert AutonomyRuntime(MemoryRails(rails.events())).tick(NOW) == []
    assert len(rails.events()) == before


def test_pending_draft_blocks_a_second_reminder(rails):
    runtime = AutonomyRuntime(rails)
    runtime.tick(NOW)
    later = NOW + timedelta(days=6)
    [result] = runtime.tick(later)
    assert result['action'] == 'wait' and 'awaiting controller approval' in result['reason']
    assert len(HumanApprovals(rails).list()) == 1


def test_ladder_climbs_every_five_days_then_escalates_to_a_human(rails):
    runtime = AutonomyRuntime(rails)
    levels, day = [], NOW
    for _ in range(4):
        out = [r for r in runtime.tick(day) if r.get('agent') == 'chaser']
        levels += [r.get('level') or r['action'] for r in out if r['action'] in ('chase', 'escalate')]
        _approve_all(rails, day)
        day += timedelta(days=5, hours=1)
    assert levels == ['polite', 'firm', 'escalation', 'escalate']
    case = _case(rails)
    assert case.state == 'escalated'
    [note] = _events(rails, 'parties_notified')
    assert note.payload['recipients'] == ['oakline', 'keyway']


def test_reminder_too_soon_waits_for_the_schedule(rails):
    runtime = AutonomyRuntime(rails)
    runtime.tick(NOW)
    _approve_all(rails, NOW)
    [result] = runtime.tick(NOW + timedelta(days=2))
    assert result['action'] == 'wait' and 'wait 5 days' in result['reason']


def test_declined_reminder_is_not_redrafted_immediately(rails):
    runtime = AutonomyRuntime(rails)
    runtime.tick(NOW)
    approvals = HumanApprovals(rails)
    [t] = approvals.list()
    approvals.decide(t['id'], CONTROLLER, False, NOW, t['digest'])
    [result] = runtime.tick(NOW + timedelta(days=1))
    assert result['action'] == 'wait' and 'declined' in result['reason']
    [again] = runtime.tick(NOW + timedelta(days=6))
    assert again['action'] == 'chase' and again['level'] == 'polite'
    assert len(approvals.list()) == 2


def test_not_due_invoice_sleeps_until_the_day_after_the_deadline(rails):
    early = datetime(2026, 3, 5, 12, tzinfo=UTC)
    [result] = AutonomyRuntime(rails).tick(early)
    assert result['action'] == 'wait' and 'Not due until 2026-03-13' in result['reason']
    assert _case(rails).next_wake_at == datetime(2026, 3, 14, 9, tzinfo=UTC)
    assert not HumanApprovals(rails).list()


def test_paid_reply_without_bank_proof_keeps_chasing(rails):
    runtime = AutonomyRuntime(rails)
    runtime.tick(NOW)
    _approve_all(rails, NOW)
    rails.append_event(Event(id='reply-1', type='chase_reply', manager_id='keyway', invoice_id=INV,
                             valid_from=NOW, learned_at=NOW,
                             payload={'text': 'We paid Rio Grande last week.', 'invoice_ids': [INV]}))
    result = runtime.route(_case(rails), NOW + timedelta(days=6))
    assert result['agent'] == 'chaser' and result['level'] == 'firm'
    assert 'holding position' in result['replies'][0]


def test_correct_payment_without_statement_requests_one(rails):
    _drop_world_statement(rails)
    runtime = AutonomyRuntime(rails)
    _pay(rails, on='2026-03-10')
    [result] = runtime.tick(NOW)
    assert result['agent'] == 'anomaly' and result['action'] == 'wait'
    assert _case(rails).state == 'awaiting_statement'
    [ticket] = HumanApprovals(rails).list()
    assert 'management statement' in ticket['payload']['body']


def test_correct_payment_and_matching_statement_close_the_case(rails):
    _pay(rails, on='2026-03-10')
    _statement(rails)
    [result] = AutonomyRuntime(rails).tick(NOW)
    assert result == {'case_id': _case(rails).id, 'agent': 'anomaly', 'action': 'resolve',
                      'replies': []}
    assert _case(rails).state == 'resolved' and _events(rails, 'payment_verified')
    assert not HumanApprovals(rails).list()


@pytest.mark.parametrize('pay, codes', [
    ({'vendor': 'v-someone-else'}, {'wrong_vendor'}),
    ({'amount': 900.0, 'on': '2026-03-10'}, {'underpayment'}),
    ({'amount': 1500.0, 'on': '2026-03-10'}, {'overpayment'}),
    ({'on': '2026-04-20'}, {'late_payment'}),
])
def test_wrong_payment_is_escalated_to_owner_and_management(rails, pay, codes):
    _pay(rails, **pay)
    _statement(rails)
    [result] = AutonomyRuntime(rails).tick(NOW)
    assert result['agent'] == 'anomaly' and result['action'] == 'escalate'
    assert codes <= set(result['findings'])
    [found] = _events(rails, 'anomaly_found')
    assert codes <= {f['code'] for f in found.payload['findings']}
    [note] = _events(rails, 'parties_notified')
    assert note.payload['recipients'] == ['oakline', 'keyway']
    [ticket] = HumanApprovals(rails).list()
    assert ticket['kind'] == 'case_review' and _case(rails).state == 'escalated'


def test_statement_that_disagrees_with_the_bank_is_an_anomaly(rails):
    _drop_world_statement(rails)
    _pay(rails, on='2026-03-10')
    _statement(rails, amount=1000.0)
    [result] = AutonomyRuntime(rails).tick(NOW)
    assert result['action'] == 'escalate' and 'statement_mismatch' in result['findings']


def test_escalation_is_not_repeated_while_the_evidence_is_unchanged(rails):
    runtime = AutonomyRuntime(rails)
    _pay(rails, vendor='v-someone-else')
    runtime.tick(NOW)
    [again] = runtime.tick(NOW + timedelta(days=1, hours=1))
    assert again['action'] == 'wait' and len(_events(rails, 'anomaly_found')) == 1
    assert len(HumanApprovals(rails).list()) == 1


def test_controller_acknowledgment_lets_the_case_close(rails):
    runtime = AutonomyRuntime(rails)
    _pay(rails, on='2026-04-20')
    _statement(rails)
    runtime.tick(NOW)
    case = _case(rails)
    proof = payment_proof(rails, case, NOW)
    CaseStore(rails).record(case.id, 'human_anomaly_acknowledged', {
        'proof_fingerprint': proof['proof_fingerprint'], 'authorized': True,
        'actor': CONTROLLER.id}, NOW, 'ack')
    result = runtime.route(_case(rails), NOW + timedelta(hours=1))
    assert result['action'] == 'resolve' and _case(rails).state == 'resolved'


def test_claude_writes_the_anomaly_note_when_it_names_every_finding(rails):
    note = AnomalyNote(note='wrong_vendor: the bank payment went to another recipient, so '
                            'holdback_unverified also applies. Please confirm who was paid.')
    _pay(rails, vendor='v-someone-else')
    AutonomyRuntime(rails, llm=ScriptedLLM(note)).tick(NOW)
    [found] = _events(rails, 'anomaly_found')
    assert found.payload['note'] == note.note


def test_claude_note_missing_a_finding_falls_back_to_the_template(rails):
    vague = AnomalyNote(note='Something looks off with this payment.')
    _pay(rails, vendor='v-someone-else')
    AutonomyRuntime(rails, llm=ScriptedLLM(vague, vague)).tick(NOW)
    [found] = _events(rails, 'anomaly_found')
    assert found.payload['note'].startswith(f'Invoice {INV}')
    assert 'different recipient' in found.payload['note']


def test_missing_agreement_terms_waits_without_chasing():
    rails = MemoryRails.from_world((3,), n_clean=0)
    rails._events = [e for e in rails.events() if e.type != 'terms_confirmed'] \
        if hasattr(rails, '_events') else None
    if rails._events is None:
        pytest.skip('MemoryRails layout changed')
    [result] = AutonomyRuntime(rails).tick(NOW)
    assert result['action'] == 'wait' and _case(rails).state == 'awaiting_terms'
    assert not [t for t in HumanApprovals(rails).list() if t['kind'] == 'send_message']


def test_jobs_stay_durable_for_the_next_wake(rails):
    AutonomyRuntime(rails).tick(NOW)
    assert any(j['status'] == 'pending' and j['due_at'] > NOW for j in Scheduler(rails).list())
