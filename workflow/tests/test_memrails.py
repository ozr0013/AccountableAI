"""MemoryRails must give the same answers rails derives from the world (no Postgres needed)."""

from datetime import UTC, date

import pytest

from rails.ledger import build_ledger
from workflow.memrails import MemoryRails
from world import generate
from world.generator import ALL_SCENARIOS, MOMENT


@pytest.fixture(scope="module")
def world():
    return generate(scenarios=ALL_SCENARIOS, n_clean=30)


@pytest.fixture(scope="module")
def rails(world):
    return MemoryRails(world.events())


def test_ledger_matches_planted(world, rails):
    got = sorted((l for m in world.managers for l in rails.ledger(m.id)), key=lambda l: l.invoice_id)
    assert got == build_ledger(world.events())


def test_unpaid_respects_as_of(rails):
    early = {l.invoice_id for l in rails.unpaid("keyway", date(2026, 3, 4))}
    late = {l.invoice_id for l in rails.unpaid("keyway", date(2026, 4, 25))}
    assert "INV-KW-0301" in early and "INV-KW-0301" in late
    assert "INV-KW-0402" not in late          # paid Apr 8


def test_replaying_an_event_is_a_noop(world):
    r = MemoryRails(world.events())
    n = len(r.events())
    for e in world.events()[:20]:
        r.append_event(e)
    assert len(r.events()) == n


def test_dependents_of_moment_fact_is_only_that_invoice(world):
    from datetime import datetime

    from contracts.models import Event
    r = MemoryRails(world.events())
    when = datetime(2026, 4, 25, 12, tzinfo=UTC)
    r.append_event(Event(id="ev-moment", type="fact_corrected", manager_id="keyway",
                         invoice_id="INV-KW-0402", valid_from=when, learned_at=when,
                         payload=MOMENT))
    assert r.dependents_of("ev-moment") == ["INV-KW-0402"]
    assert r.ledger("keyway") and r.reconcile("keyway")["diff"] == 0
