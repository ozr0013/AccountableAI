from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from agents.context import build_context
from agents.skill_registry import load_skill
from agents.supervisor import Supervisor
from contracts.models import Document, Event, LedgerLine, SourceKind
from workflow.cases import CaseStore
from workflow.memrails import MemoryRails

NOW = datetime(2026, 10, 24, tzinfo=UTC)


class Client:
    def __init__(self, script):
        self.script = iter(script)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        name, args = next(self.script)
        return SimpleNamespace(model='fake-native', usage=SimpleNamespace(input_tokens=123, output_tokens=45), content=[{'type': 'tool_use', 'id': str(len(self.calls)),
                                         'name': name, 'input': args}])


class Actions:
    def __init__(self):
        self.calls = []

    def request_payment_proof(self, case, now, key):
        self.calls.append('payment_proof')
        return {'status': 'queued'}

    def request_statement(self, case, now, key):
        self.calls.append('statement')
        return {'status': 'queued'}


def setup(paid=False):
    rails = MemoryRails([Event(id='terms', type='terms_confirmed', manager_id='m',
        valid_from=NOW, learned_at=NOW, payload={'vendor_payment_days': 10, 'approval_limit': 1000})])
    line = LedgerLine(invoice_id='i', manager_id='m', vendor_id='v', amount=100)
    docs = [Document(id='invoice', kind=SourceKind.vendor_invoice, manager_id='m',
                     vendor_id='v', data={'invoice_id': 'i', 'amount': 100}),
            Document(id='claim', kind=SourceKind.manager_reply, manager_id='m',
                     vendor_id='v', data={'invoice_id': 'i'}, text='Already paid')]
    if paid:
        docs += [Document(id='bank', kind=SourceKind.manager_bank, manager_id='m',
                 data={'payments': [{'invoice_id': 'i', 'vendor_id': 'v', 'amount': 100,
                                     'on': '2026-10-23'}]})]
    rails.documents = lambda manager_id: docs
    rails.ledger = lambda manager_id: [line]
    case = CaseStore(rails).ensure(line, NOW)
    return rails, case


def test_claim_native_lookup_request_wait():
    rails, case = setup()
    actions = Actions()
    client = Client([('read', {'kind': 'evidence'}), ('read', {'kind': 'payment'}),
                     ('request', {'kind': 'payment_proof', 'reason': 'Claim lacks proof'}),
                     ('wait', {'until': (NOW + timedelta(days=1)).isoformat(), 'reason': 'Await proof'})])
    result = Supervisor(rails, client, actions).run(case.id, NOW)
    assert result['state'] == 'awaiting_payment_proof'
    assert actions.calls == ['payment_proof']
    assert client.calls[1]['messages'][2]['content'][0]['type'] == 'tool_result'
    assert any(e.type == 'supervisor_context' for e in rails.events())


def test_bank_stops_chasing_and_requests_statement():
    rails, case = setup(True)
    actions = Actions()
    client = Client([('request', {'kind': 'payment_proof', 'reason': 'Chase'}),
                     ('read', {'kind': 'payment'}),
                     ('request', {'kind': 'statement', 'reason': 'Need reconciliation'}),
                     ('wait', {'until': (NOW + timedelta(days=1)).isoformat(), 'reason': 'Await statement'})])
    result = Supervisor(rails, client, actions).run(case.id, NOW)
    assert actions.calls == ['statement']
    assert result['state'] == 'awaiting_statement'


def test_bounded_resume_and_fail_closed():
    rails, case = setup()
    client = Client([('request', {'kind': 'statement', 'reason': 'Need it'})])
    result = Supervisor(rails, client, max_steps=1).run(case.id, NOW)
    assert result['status'] == 'scheduled'
    assert CaseStore(rails).get(case.id).next_wake_at > NOW
    assert any(e.payload.get('result', {}).get('error') == 'actions_unconfigured'
               for e in rails.events())


def test_skill_and_context_budget():
    _rails, case = setup()
    with pytest.raises(ValueError):
        load_skill('../secret')
    context = build_context(case, [{'source_id': 'a', 'text': 'x' * 50000}])
    assert len(context['text']) <= 16000
    assert context['manifest']['skills'][0]['sha256']


def test_scope_and_invalid_resolution():
    rails, case = setup()
    docs = rails.documents('m') + [
        Document(id='foreign', kind=SourceKind.manager_reply, manager_id='other',
                 data={'invoice_id': 'i'}, text='secret'),
        Document(id='shared', kind=SourceKind.manager_bank, manager_id='m', text='secret',
                 data={'payments': [{'invoice_id': 'i', 'vendor_id': 'v', 'amount': 1},
                                    {'invoice_id': 'other', 'amount': 999}]})]
    rails.documents = lambda manager_id: docs
    supervisor = Supervisor(rails, Client([('resolve', {'reason': 'claim'})]), max_steps=1)
    scoped = supervisor._docs(case, NOW)
    assert 'foreign' not in [d['id'] for d in scoped]
    bank = next(d for d in scoped if d['id'] == 'shared')
    assert bank['text'] == ''
    assert len(bank['data']['payments']) == 1
    assert supervisor.run(case.id, NOW)['status'] == 'scheduled'
    assert CaseStore(rails).get(case.id).state != 'resolved'


def test_plan_citations_and_specialist_usage_receipts():
    rails, case = setup()
    client = Client([('update_plan', {'summary': 'Paid claim needs proof',
        'steps': ['Check bank proof'], 'open_questions': ['Where is payment?'],
        'source_ids': ['claim']}), ('analyze', {'kind': 'trail'})])
    called = []
    specialist = lambda case, line, docs, now: called.append(line.invoice_id) or {'source_ids': ['claim']}
    result = Supervisor(rails, client, max_steps=2, specialists={'trail': specialist}).run(case.id, NOW)
    assert result['status'] == 'scheduled'
    saved = CaseStore(rails).get(case.id)
    assert saved.plan == ['Check bank proof']
    assert 'claim' in saved.summary
    assert called == ['i']
    receipts = [e for e in rails.events() if e.type == 'supervisor_model_call']
    assert len(receipts) == 2 and receipts[0].payload['tool_ids'] == ['1']
    assert receipts[0].payload['context_sha256']
    assert receipts[0].payload['input_tokens'] == 123
    assert receipts[0].payload['output_tokens'] == 45
    outcome, _ = Supervisor(rails)._execute('update_plan', {'summary': 'bad', 'steps': [],
        'open_questions': [], 'source_ids': ['other-case']}, saved, NOW, 'bad')
    assert 'error' in outcome


def test_override_capabilities_and_native_usage():
    _rails, case = setup()
    context = build_context(case, [], skill_override={'name': 'reviewed', 'version': '2',
        'capabilities': ['read'], 'text': 'Read evidence first.'})
    assert context['manifest']['skills'][0]['version'] == '2'
    with pytest.raises(ValueError):
        build_context(case, [], skill_override={'version': '3', 'capabilities': ['send_money'],
                                              'text': 'Disallowed'})


def test_anomaly_acknowledgment_matches_current_proof():
    from contracts.models import Event
    rails, case = setup(True)
    docs = rails.documents('m')
    bank = next(d for d in docs if d.id == 'bank')
    bank.data['payments'][0]['amount'] = 110
    docs.append(Document(id='statement', kind=SourceKind.manager_statement, manager_id='m',
        dated=NOW.date(), data={'lines': [{'invoice_id': 'i', 'status': 'paid', 'amount': 110}]}))
    supervisor = Supervisor(rails)
    proof = supervisor._proof(case, NOW)
    assert proof['valid_bank_proof'] and proof['anomalies']
    outcome, _ = supervisor._execute('resolve', {'reason': 'reviewed'}, case, NOW, 'r1')
    assert 'error' in outcome
    rails.append_event(Event(id='ack', type='human_anomaly_acknowledged', manager_id='m',
        invoice_id='i', valid_from=NOW, learned_at=NOW, payload={'case_id': case.id,
        'authorized': True, 'proof_fingerprint': proof['proof_fingerprint']}))
    assert supervisor._proof(case, NOW)['anomalies_acknowledged']
    outcome, terminal = supervisor._execute('resolve', {'reason': 'reviewed'}, case, NOW, 'r2')
    assert terminal and outcome['status'] == 'resolved'


def test_plan_preserves_evidence_hash_and_reopen_guard():
    rails, case = setup()
    case = CaseStore(rails).update(case.id, case.version, NOW,
                                 last_observation={'evidence_hash': 'runtime-fingerprint'})
    Supervisor(rails)._execute('update_plan', {'summary': 'Cited', 'steps': [],
        'open_questions': [], 'source_ids': ['claim']}, case, NOW, 'plan')
    assert CaseStore(rails).get(case.id).last_observation['evidence_hash'] == 'runtime-fingerprint'


def test_wrong_and_unknown_recipients_visible_but_never_proof():
    rails, case = setup()
    docs = rails.documents('m')
    docs.append(Document(id='mixed', kind=SourceKind.manager_bank, manager_id='m',
        data={'payments': [{'invoice_id': 'i', 'vendor_id': 'wrong', 'amount': 100, 'on': '2026-10-23'},
                           {'invoice_id': 'i', 'amount': 100, 'on': '2026-10-23'},
                           {'invoice_id': 'other', 'vendor_id': 'private', 'amount': 999}]}))
    supervisor = Supervisor(rails)
    scoped = next(d for d in supervisor._docs(case, NOW) if d['id'] == 'mixed')
    assert len(scoped['data']['payments']) == 2
    proof = supervisor._proof(case, NOW)
    assert not proof['valid_bank_proof']
    assert {'wrong_vendor', 'recipient_unknown'} <= {a['code'] for a in proof['anomalies']}


def test_missing_terms_cannot_be_resolved_even_after_acknowledgment():
    rails, case = setup(True)
    rails._events = [e for e in rails._events if e.type != 'terms_confirmed']
    supervisor = Supervisor(rails)
    proof = supervisor._proof(case, NOW)
    assert 'terms_missing' in proof['resolution_blockers']
    rails.append_event(Event(id='ack', type='human_anomaly_acknowledged', manager_id='m',
        invoice_id='i', valid_from=NOW, learned_at=NOW, payload={'case_id': case.id,
        'authorized': True, 'proof_fingerprint': proof['proof_fingerprint']}))
    result, terminal = supervisor._execute('resolve', {'reason': 'Override'}, case, NOW, 'resolve')
    assert not terminal and 'error' in result


def test_lawful_holdback_stops_chasing_but_waits_for_release():
    rails, case = setup(True)
    rails.append_event(Event(id='holdback', type='terms_confirmed', manager_id='m',
        valid_from=NOW, learned_at=NOW + timedelta(seconds=1),
        payload={'vendor_payment_days': 10, 'approval_limit': 1000, 'holdback_pct': 10,
                 'holdback_condition': 'until inspection passes'}))
    docs = rails.documents('m')
    next(d for d in docs if d.id == 'invoice').data['inspection'] = 'pending'
    next(d for d in docs if d.id == 'bank').data['payments'][0]['amount'] = 90
    supervisor = Supervisor(rails, actions=Actions())
    proof = supervisor._proof(case, NOW + timedelta(seconds=2))
    assert proof['valid_bank_proof'] and proof['required_amount'] == '90'
    assert 'holdback_pending' in proof['resolution_blockers']
    result, _ = supervisor._execute('request', {'kind': 'payment_proof', 'reason': 'Chase'},
                                    case, NOW + timedelta(seconds=2), 'chase')
    assert 'error' in result
    next(d for d in docs if d.id == 'invoice').data['inspection'] = 'passed'
    proof = supervisor._proof(case, NOW + timedelta(seconds=2))
    assert not proof['valid_bank_proof'] and proof['required_amount'] == '100'


def test_unverified_holdback_and_bad_dates_do_not_suppress_chasing():
    rails, case = setup(True)
    rails.append_event(Event(id='holdback-unverified', type='terms_confirmed', manager_id='m',
        valid_from=NOW, learned_at=NOW + timedelta(seconds=1),
        payload={'holdback_pct': 10, 'vendor_payment_days': 10}))
    docs = rails.documents('m')
    next(d for d in docs if d.id == 'invoice').data['inspection'] = 'pending'
    payment = next(d for d in docs if d.id == 'bank').data['payments'][0]
    payment['amount'] = 90
    proof = Supervisor(rails)._proof(case, NOW + timedelta(seconds=2))
    assert proof['required_amount'] == '100' and not proof['valid_bank_proof']
    assert 'holdback_unverified' in proof['resolution_blockers']
    payment['amount'], payment['on'] = 100, '2026-00-00'
    proof = Supervisor(rails)._proof(case, NOW + timedelta(seconds=2))
    assert not proof['valid_bank_proof']
    assert 'invalid_payment_details' in {f['code'] for f in proof['anomalies']}


def test_maximal_valid_plan_does_not_break_restart_context():
    rails, case = setup()
    args = {'summary': 's' * 2000, 'steps': ['x' * 500] * 12,
            'open_questions': ['q' * 500] * 12, 'source_ids': ['invoice']}
    supervisor = Supervisor(rails)
    result, _ = supervisor._execute('update_plan', args, case, NOW, 'maximal')
    assert result['status'] == 'saved'
    context = build_context(CaseStore(rails).get(case.id), [])
    assert len(context['text']) <= 16000


def test_other_case_observations_are_not_loaded_into_context():
    rails, case = setup()
    other = CaseStore(rails).ensure(LedgerLine(invoice_id='i', manager_id='m', vendor_id='else', amount=1), NOW)
    CaseStore(rails).record(other.id, 'supervisor_tool_result', {'result': 'private'}, NOW, 'other')
    assert all(e.payload.get('case_id') != other.id for e in Supervisor(rails)._history(case, NOW))


def test_decision_reads_cannot_bypass_shared_document_redaction():
    import json
    rails, case = setup()
    rails.append_event(Event(id='shared-ingest',type='document_ingested',manager_id='m',invoice_id='i',
        valid_from=NOW,learned_at=NOW,payload={'kind':'manager_bank','data':{'invoice_id':'i',
        'payments':[{'invoice_id':'else','vendor_id':'SECRET_CANARY','amount':999}]}}))
    result, _ = Supervisor(rails)._execute('read',{'kind':'decisions'},case,NOW,'redaction')
    assert 'SECRET_CANARY' not in json.dumps(result)


def test_same_number_vendor_invoice_requires_review_without_exposing_other_rows():
    import json
    rails,case=setup(True)
    original=rails.documents('m')
    original += [Document(id='other-invoice',kind='vendor_invoice',manager_id='m',vendor_id='other',data={'invoice_id':'i','amount':999}),
                 Document(id='other-bank',kind='manager_bank',manager_id='m',data={'payments':[{'invoice_id':'i','vendor_id':'other','amount':999,'on':'2026-10-23'}],'private_note':'SECRET_CANARY'})]
    rails.documents=lambda manager_id:original
    supervisor=Supervisor(rails)
    assert 'invoice_namespace_conflict' in supervisor._proof(case,NOW)['resolution_blockers']
    assert 'SECRET_CANARY' not in json.dumps(supervisor._docs(case,NOW))
    assert 'other-bank' not in json.dumps(supervisor._docs(case,NOW))
