"""Task 7: selective recheck. The judge injects one fact; only dependents rerun; the one alert it
undermines is withdrawn with a reason; the others are untouched."""

import pytest

from agents import RealAgents
from rails.console import JudgeConsole
from workflow.book import WorkflowBook
from workflow.memrails import MemoryRails
from world import generate
from world.generator import ALL_SCENARIOS, MOMENT

from .conftest import make_workflow

# The moment: $3,140 open across three items; the judge's fact withdraws the electrician only.
ELECTRICIAN, PLUMBER, CLEANER = "INV-KW-0402", "INV-KW-0301", "INV-KW-0901"


@pytest.fixture
def world():
    rails = MemoryRails()
    wf = make_workflow(rails, agents=RealAgents(None))
    wf.replay(generate(scenarios=ALL_SCENARIOS, n_clean=30).events())
    return rails, wf


def _open(rails):
    return {i: a for a in WorkflowBook.load(rails).open_alerts() for i in a.draft.invoice_ids}


def _moment(rails, wf):
    res = JudgeConsole(rails).apply_preset("invoice_received_correction")   # Apr 15, not Mar 28
    return res, [wf.handle(e) for e in res.events]


def test_before_the_moment_all_three_items_are_open(world):
    rails, _ = world
    assert {ELECTRICIAN, PLUMBER, CLEANER} <= set(_open(rails))


def test_moment_withdraws_only_the_electrician_with_reason(world):
    rails, wf = world
    before = _open(rails)
    log_before = list(rails.events())
    res, [r] = _moment(rails, wf)
    assert res.affected == [ELECTRICIAN] and r.route == "assess"
    assert r.withdrawn == [before[ELECTRICIAN].draft.id]
    after = _open(rails)
    assert ELECTRICIAN not in after
    assert set(after) == set(before) - {ELECTRICIAN}
    assert after[PLUMBER] == before[PLUMBER] and after[CLEANER] == before[CLEANER]   # untouched
    [w] = [e for e in rails.events() if e.type == "alert_withdrawn" and e not in log_before]
    assert "2026-04-25" in w.payload["reason"]                         # new due date explained
    touched = {e.invoice_id for e in rails.events() if e not in log_before and e.invoice_id}
    assert touched == {ELECTRICIAN}                                    # nothing else rechecked


def test_moment_is_idempotent(world):
    rails, wf = world
    _moment(rails, wf)
    n = len(rails.events())
    _, rs = _moment(rails, wf)                                         # same click again
    assert all(r.skipped for r in rs) and len(rails.events()) == n


def test_terms_change_rechecks_the_whole_manager(world):
    rails, wf = world
    res = JudgeConsole(rails).apply_preset("terms_changed", {"manager_id": "keyway", "days": "45"})
    [r] = [wf.handle(e) for e in res.events]
    assert PLUMBER in res.affected and len(res.affected) > 2
    assert PLUMBER in _open(rails)                     # Mar 3 + 45 days (Apr 17) is still past due on Apr 22
    assert ELECTRICIAN in r.withdrawn or ELECTRICIAN not in _open(rails)


def test_moment_fact_matches_world_truth():
    assert MOMENT["fact_key"] == f"invoice:{ELECTRICIAN}.received"


def test_checkpoint_b_fallback_reruns_whole_manager_and_reports_changes(world, monkeypatch):
    rails, wf = world
    monkeypatch.setenv("RECHECK_MODE", "manager")
    before = _open(rails)
    _, [r] = _moment(rails, wf)
    keyway = {l.invoice_id for l in rails.ledger("keyway") if not l.duplicate_of}
    [done] = [e for e in rails.events() if e.type == "workflow_done"
              and e.payload["trigger"] == r.event_id]
    assert set(done.payload["rechecked"]) == keyway and len(keyway) > 2
    assert done.payload["changed"] == [ELECTRICIAN]                   # same outcome as selective
    assert _open(rails)[PLUMBER] == before[PLUMBER] and _open(rails)[CLEANER] == before[CLEANER]
    assert any(t.startswith("recheck:") and "1 changed" in t for t in r.trace)


def test_moment_moves_the_hold_from_3140_to_2240(world):
    from workflow.run import funding_event
    rails, wf = world
    as_of = rails.now()
    wf.replay([funding_event("keyway", 18400, as_of)])
    _moment(rails, wf)
    after_ev = funding_event("keyway", 18400, as_of).model_copy(update={"id": "ev-funding-after"})
    wf.replay([after_ev])
    holds = [e.payload["case"]["proposed_hold"] for e in rails.events() if e.type == "case_proposed"]
    assert holds == [3140.0, 2240.0]
