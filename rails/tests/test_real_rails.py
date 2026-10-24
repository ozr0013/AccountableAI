"""RAILS=real must answer the same calls as the stand-in on the shared scenario 3 fixture."""

from datetime import UTC, date

import pytest

from contracts.fakes import FakeRails
from contracts.registry import rails as registry_rails
from rails import RealRails
from world import generate

AS_OF = date(2026, 4, 22)      # world.generator.WORLD_TODAY


@pytest.fixture
def real(schema):
    r = RealRails(schema=schema)
    r.load(generate(n_clean=0).events())
    return r


def test_conforms_to_fake_on_scenario_3(real):
    fake = FakeRails()
    assert real.ledger("keyway") == fake.ledger("keyway")
    assert real.unpaid("keyway", AS_OF) == fake.unpaid("keyway", AS_OF)
    assert real.late_months("keyway") == fake.late_months("keyway")
    f, r = fake.reconcile("keyway"), real.reconcile("keyway")
    assert {k: r[k] for k in f} == f
    assert real.dependents_of("invoice:INV-KW-0301.received") == fake.dependents_of("x")
    for case in [("move_money", "agent", {}), ("approve", "a", {"proposed_by": "a"}), ("read", "a", {}),
                 ("approve", "controller", {"proposed_by": "case_agent"}),
                 ("send_unapproved", "chaser", {})]:
        assert real.policy_check(*case)[0] == fake.policy_check(*case)[0]
    assert {d.id for d in real.documents("keyway")} == {d.id for d in fake.documents("keyway")}
    assert [d.id for d in real.documents("keyway", "INV-KW-0301")] == \
           [d.id for d in fake.documents("keyway", "INV-KW-0301")]


def test_now_comes_from_the_world_not_the_wall_clock(real, monkeypatch):
    assert real.now() == AS_OF
    monkeypatch.setenv("WORLD_AS_OF", "2026-04-20")
    assert real.now() == date(2026, 4, 20)


def test_unpaid_respects_as_of(real):
    assert real.unpaid("keyway", date(2026, 3, 1)) == []           # invoice not received yet
    assert len(real.unpaid("keyway", date(2026, 3, 20))) == 1


def test_registry_loads_real_with_env(monkeypatch):
    monkeypatch.setenv("RAILS", "real")
    try:
        assert type(registry_rails()).__name__ == "RealRails"
    except Exception as e:      # no database in this environment
        pytest.skip(str(e))


def test_append_event_is_idempotent_and_indexed(real):
    ev = next(e for e in real.events("keyway") if e.type == "document_ingested")
    real.append_event(ev)
    assert len(real.events("keyway")) == len({e.id for e in real.events("keyway")})


def test_stand_in_chaser_templates_pass_the_real_gate(real):
    from contracts.fakes import FakeAgents
    msg = FakeAgents().draft_chase("keyway", [], [])
    assert real.policy_check("send_chase", "chaser", {"template_id": msg.template_id})[0]


def test_reply_from_stand_in_reader_reaches_trust_rules(real):
    from datetime import datetime

    from contracts.fakes import FakeAgents
    from contracts.models import Event
    reply = FakeAgents().read_reply("We paid it.", "keyway", [])       # invoice_ids == []
    ts = datetime(2026, 4, 24, tzinfo=UTC)
    real.append_event(Event(id=reply.id, type="chase_reply", manager_id="keyway",
                            invoice_id="INV-KW-0301", valid_from=ts, learned_at=ts,
                            payload=reply.model_dump(mode="json")))
    assert any(d.kind == "manager_reply" for d in real.documents("keyway", "INV-KW-0301"))
    assert "INV-KW-0301" in real.dependents_of(reply.id)


def test_loading_a_different_world_is_refused_clearly(real):
    from rails import WorldMismatch
    real.load(generate(n_clean=0).events())                    # same world again: fine
    with pytest.raises(WorldMismatch, match="--fresh"):
        real.load(generate(n_clean=5).events())


def test_memory_rails_answers_like_real_rails_on_the_demo_world(schema):
    """Kanishka's in-memory rails (used by the workflow tests and Omar's harness) must give the
    same answers as the Postgres rails, or the scores don't describe the demo."""
    from workflow.memrails import MemoryRails
    from world.generator import ALL_SCENARIOS
    evs = generate(13, ALL_SCENARIOS, 100).events()
    real, mem = RealRails(schema=schema), MemoryRails(evs)
    real.load(evs)
    assert real.ledger("keyway") == mem.ledger("keyway")
    assert real.unpaid("keyway", AS_OF) == mem.unpaid("keyway", AS_OF)
    assert real.late_months("keyway") == mem.late_months("keyway")
    assert real.reconcile("keyway") == mem.reconcile("keyway")
    assert real.now() == mem.now() == AS_OF
    keys = ["invoice:INV-KW-0402.received", "invoice:INV-KW-0301.payment",
            "terms:keyway.vendor_payment_days", "ev-fact-kw-0402-received", "ev-doc-doc-bank-kw-2026-03"]
    for k in keys:
        assert real.dependents_of(k) == mem.dependents_of(k), k
    assert {d.id for d in real.documents("keyway")} == {d.id for d in mem.documents("keyway")}


def test_append_and_dependency_index_rollback_together(schema, monkeypatch):
    from datetime import UTC, datetime

    from contracts.models import Event
    from rails.real import RealRails
    rails = RealRails(schema=schema)
    event = Event(id='atomic-index-test', type='terms_confirmed', manager_id='m',
                  valid_from=datetime(2026,4,1,tzinfo=UTC), learned_at=datetime(2026,4,1,tzinfo=UTC),
                  payload={'vendor_payment_days':10})
    original = rails.deps.index_event
    def fail_after_index(event):
        original(event)
        raise RuntimeError('index write failed')
    monkeypatch.setattr(rails.deps, 'index_event', fail_after_index)
    import pytest
    with pytest.raises(RuntimeError, match='index write failed'):
        rails.append_event(event)
    assert rails.store.get(event.id) is None
    assert not rails.store.conn.execute('SELECT 1 FROM event_facts WHERE event_id=%s', (event.id,)).fetchall()
    monkeypatch.setattr(rails.deps, 'index_event', original)
    rails.append_event(event)
    assert rails.store.get(event.id) is not None
    rails.store.conn.close()
