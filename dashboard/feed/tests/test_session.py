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
