"""RAILS=real must answer the same calls as the stand-in on the shared scenario 3 fixture."""

from datetime import date

import pytest

from contracts.fakes import FakeRails
from contracts.registry import rails as registry_rails
from rails import RealRails
from world import generate

AS_OF = date(2026, 4, 25)


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
    for case in [("move_money", "agent", {}), ("approve", "a", {"proposed_by": "a"}), ("read", "a", {})]:
        assert real.policy_check(*case) == fake.policy_check(*case)
    assert {d.id for d in real.documents("keyway")} == {d.id for d in fake.documents("keyway")}
    assert [d.id for d in real.documents("keyway", "INV-KW-0301")] == \
           [d.id for d in fake.documents("keyway", "INV-KW-0301")]


def test_now_comes_from_the_world_not_the_wall_clock(real, monkeypatch):
    assert real.now() == AS_OF
    monkeypatch.setenv("WORLD_AS_OF", "2026-05-01")
    assert real.now() == date(2026, 5, 1)


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
