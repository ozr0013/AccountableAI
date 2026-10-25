"""The controller demo loop, without credentials or paid model calls."""
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from session import DemoSession


@pytest.fixture
def demo():
    return DemoSession(n_clean=0)


def pending(demo, kind):
    return next(c for c in demo.snapshot()['cases']
                if c['kind'] == kind and c['status'] == 'pending')


def test_switch_modes_preserves_events_and_checkpoints(demo, monkeypatch):
    import live
    monkeypatch.setattr(live, 'available', lambda: True)
    monkeypatch.setattr(live, 'MeteredLLM', lambda meter: None)
    monkeypatch.setattr(live, 'MeteredVerifier', lambda meter, fallback: fallback)
    events = demo.rails.events()
    checkpoint = demo.wf.graph.checkpointer
    assert demo.set_live(True)['ok']
    assert demo.live
    assert demo.wf.deps is demo.deps
    assert demo.wf.graph.checkpointer is checkpoint
    assert demo.rails.events() == events
    assert demo.set_live(False)['ok']
    assert demo.deps.agents is demo.rules_agents
    assert not demo.live


def test_snapshot_never_calls_live_agents(demo):
    from workflow import Deps
    agents = Mock()
    agents.build_trail.side_effect = AssertionError('read invoked live model')
    agents.classify_cause.side_effect = AssertionError('read invoked live model')
    demo.deps = Deps(demo.rails, agents, demo.rules_verifier, demo.approvals)
    assert demo.snapshot()['items']
    assert not agents.mock_calls


def test_bank_approval_requires_identity_and_can_retry(demo):
    aid = pending(demo, 'bank_change')['approval_id']
    before = len(demo.rails.events())
    assert not demo.decide(aid, True)['ok']
    assert len(demo.rails.events()) == before
    assert aid in demo.approvals.pending()
    result = demo.decide(aid, True, identity_verified=True)
    assert result['ok']
    assert result['state']['decisions'][-1]['allowed']
    assert not demo.decide(aid, True, identity_verified=True)['ok']


def test_agent_cannot_decline_controller_case(demo):
    aid = pending(demo, 'funding')['approval_id']
    assert not demo.decide(aid, False, by='case_agent')['ok']
    assert aid in demo.approvals.pending()


def test_fact_rechecks_funding_then_records_controller_instruction(demo):
    old = pending(demo, 'funding')['approval_id']
    result = demo.console('invoice_received_correction')
    assert result['ok']
    assert result['diff']['unexplained'] == [3140.0, 2240.0]
    assert result['responses']
    assert not demo.decide(old, True)['ok']
    aid = pending(demo, 'funding')['approval_id']
    result = demo.decide(aid, True)
    instruction = result['state']['decisions'][-1]
    assert instruction['hold'] == 2240
    assert instruction['release'] == 16160


def test_reply_requires_proof_to_settle_then_reset(demo):
    # Select the disputed plumbing invoice from the generated world.
    invoice = next(i['id'] for i in demo.snapshot()['items']
                   if i['owed'] == 1240 and i['cause'] == 'unexplained')
    result = demo.reply(invoice, 'We paid it yesterday.')
    assert result['ok']
    assert next(i for i in result['state']['items'] if i['id'] == invoice)['owed'] == 1240
    assert any(r['judge'] for r in result['state']['replies'])
    result = demo.reply(invoice, 'Here is the bank payment.', 'bank')
    assert result['ok']
    assert next(i for i in result['state']['items'] if i['id'] == invoice)['owed'] == 0
    assert demo.advance(5)['ok']
    demo.reset()
    state = demo.snapshot()
    assert state['history'] == []
    assert state['decisions'] == []
    assert state['headline']['unexplained'] == 3140


def test_a_session_never_emails_unless_the_server_turns_it_on(demo, monkeypatch):
    import mailer
    sent = Mock(side_effect=AssertionError("a test session tried to send a real email"))
    monkeypatch.setattr(mailer, "send", sent)
    monkeypatch.setattr(mailer, "enabled", lambda: True)      # as if the send token existed
    demo.advance(5)                                           # reminders go out on this step
    assert not sent.called and demo.send_email is False


def test_a_document_reports_the_books_before_and_after():
    """Live investigation shows these: a claim with no proof moves nothing, a bank record moves one invoice."""
    demo = DemoSession()                         # the full world the dashboard runs
    samples = Path(__file__).resolve().parents[1] / 'samples'
    said = demo.add_file(samples / 'Keyway_Reply_KW-0901.eml')
    assert said['books']['before'] == said['books']['after']
    assert said['diff']['changed'] == []
    bank = demo.add_file(samples / 'Keyway_Bank_Export_Apr22.csv')
    b, a = bank['books']['before'], bank['books']['after']
    assert [c['id'] for c in bank['diff']['changed']] == ['INV-KW-0901']
    # $1,000 proven paid: it moves from "left to pay" (and past due) to "paid to vendors", nothing else
    assert (a['paid'] - b['paid'], b['left'] - a['left'], b['late'] - a['late']) == (1000, 1000, 1000)
    assert (a['funded'], a['not_funded'], b['unexplained'] - a['unexplained']) == (b['funded'], b['not_funded'], 1000)
    assert b['attention'] - a['attention'] == 1


@pytest.fixture(scope='module')
def full():
    return DemoSession()                         # the full world the dashboard runs


def test_an_action_returns_its_run_as_an_ordered_timeline(full):
    """Live investigation plays this back: what the reader read, then each step, agent and gate check."""
    full.reset()
    out = full.add_file(Path(__file__).resolve().parents[1] / 'samples' / 'Keyway_Bank_Export_Apr22.csv')
    t = out['timeline']
    assert t[0]['type'] == 'reader' and t[0]['kind'] == ['manager_bank']
    assert t[1] == {'type': 'step', 'step': 'route'}
    decisions = [x for x in t if x['type'] == 'decision']
    assert {'trail', 'cause', 'verifier', 'case'} <= {d['agent'] for d in decisions}
    assert all(d['steps'] and d['question'] for d in decisions)
    gate = [x for x in t if x['type'] == 'policy']
    assert {(g['action'], g['ok']) for g in gate} == {('send_chase', False), ('propose_hold', True)}
    assert all(g['why'] for g in gate)            # a reminder too soon is refused, with the reason
    # each agent's decision follows its own start
    starts = [i for i, x in enumerate(t) if x['type'] == 'agent_started']
    assert starts and all(t[i + 1]['type'] == 'decision' and t[i + 1]['agent'] == t[i]['agent'] for i in starts)
    assert 'policy_check' not in vars(full.rails)          # the gate is the rails' own again


def test_an_email_the_judge_writes_goes_through_the_reader(full):
    full.reset()
    said = full.write_email('keyway', 'Re: KW-0402', 'Hi, Sparkline Electric was paid for KW-0402 last week.')
    assert said['ok'] and said['kind'] == 'reply' and said['timeline'][0]['type'] == 'reader'
    assert said['books']['before'] == said['books']['after']               # a claim alone moves nothing
    rio = full.write_email('v-riogrande', 'Payment received - KW-0301',
                           'We received your payment of $1,240.00 for KW-0301 today. Thank you.')
    assert rio['ok'] and [c['id'] for c in rio['diff']['changed']] == ['INV-KW-0301']
    assert rio['books']['before']['unexplained'] - rio['books']['after']['unexplained'] == 1240
    vague = full.write_email('keyway', 'hello', 'Everything is paid, thanks.')
    assert not vague['ok'] and vague['error']


def test_an_upload_must_be_a_file_the_reader_takes(full):
    assert not full.upload('notes.docx', b'x')['ok']
    assert not full.upload('big.pdf', b'x' * 2_000_001)['ok']
    assert full.upload('../../etc/passwd.csv', b'Date,Amount\n')['ok'] is False      # nothing usable
    assert not list(full.inbox.parent.glob('passwd.csv'))                          # stayed in the inbox
