"""The workflow with Kanishka's real agents (rules mode, no API key) over all 9 scenarios in the
one-middleman demo world (Keyway, demo date Apr 22; see world/scenario_spec.json)."""

import pytest

from agents import RealAgents
from contracts.models import AlertKind
from workflow.book import WorkflowBook
from workflow.memrails import MemoryRails
from workflow.run import scan_events
from world import generate
from world.generator import ALL_SCENARIOS

from .conftest import make_workflow


@pytest.fixture(scope="module")
def outcome():
    w = generate(scenarios=ALL_SCENARIOS, n_clean=30)
    rails = MemoryRails()
    wf = make_workflow(rails, agents=RealAgents(None))
    wf.replay(w.events() + scan_events([m.id for m in w.managers], w.as_of))
    book = WorkflowBook.load(rails)
    items = [a for a in book.open_alerts() if a.draft.kind != AlertKind.recurring_pattern]
    return {i: a for a in items for i in a.draft.invoice_ids}, book


def test_no_manager_alert_for_hold_or_owner_side(outcome):
    alerts, _ = outcome
    assert "INV-KW-0101" not in alerts                                   # scenario 1
    assert alerts["INV-KW-0601"].draft.kind == AlertKind.owner_pending_approval   # scenario 6


def test_paperwork_gets_a_document_request_not_a_manager_alert(outcome):
    alerts, _ = outcome
    assert alerts["INV-KW-0201"].draft.kind == AlertKind.paperwork_request        # scenario 2


def test_unexplained_items_alert_and_get_chased(outcome):
    alerts, book = outcome
    for inv in ("INV-KW-0301", "INV-KW-0901"):                          # scenarios 3, 9
        assert alerts[inv].draft.kind == AlertKind.unexplained_unpaid
    s3 = {c.source_id for cl in alerts["INV-KW-0301"].draft.claims for c in cl.citations}
    assert {"doc-stmt-kw-2026-03", "doc-bank-kw-2026-03", "doc-notice-riogrande-0420"} <= s3
    assert book.chases["keyway"] and set(book.chases) == {"keyway"}


def test_scenario_7_held_payment_is_not_blamed_on_the_manager(outcome):
    alerts, _ = outcome
    assert alerts["INV-KW-0701"].draft.kind == AlertKind.bank_detail_change


def test_scenario_5_paid_within_the_window_gets_no_alert(outcome):
    alerts, _ = outcome
    assert "INV-KW-0501" not in alerts                                   # paid Apr 8, due Apr 11


def test_moment_item_is_open_before_the_judge_speaks(outcome):
    alerts, _ = outcome
    assert alerts["INV-KW-0402"].draft.kind == AlertKind.unexplained_unpaid      # due Apr 7, unpaid
    owed = {i: alerts[i].draft.amount for i in ("INV-KW-0301", "INV-KW-0402", "INV-KW-0901")}
    assert owed == {"INV-KW-0301": 1240.0, "INV-KW-0402": 900.0, "INV-KW-0901": 1000.0}  # $3,140


def test_scenario_4_pattern_alert_against_the_agreement_window(outcome):
    _, book = outcome
    patterns = [a for a in book.open_alerts() if a.draft.kind == AlertKind.recurring_pattern]
    assert [a.draft.manager_id for a in patterns] == ["keyway"]
    [p] = patterns
    assert "3 of 4 months" in p.draft.headline                          # late Jan-Mar, April on time
    assert "Apr 2026" not in p.draft.headline
    kinds = {c.kind for cl in p.draft.claims for c in cl.citations}
    assert {"manager_bank", "agreement"} <= kinds
    # Keyway's individual late-but-paid invoices are folded into the pattern
    assert not [a for a in book.open_alerts("keyway")
                if a.draft.kind == AlertKind.unexplained_unpaid and a.draft.amount == 0]


def test_scenario_8_duplicate_billing_cites_both_fundings(outcome):
    alerts, _ = outcome
    d = alerts["INV-KW-0802"]
    assert d.draft.kind == AlertKind.duplicate_billing and d.draft.amount == 320.0
    cited = {c.source_id for cl in d.draft.claims for c in cl.citations}
    assert {"doc-funding-kw-0801", "doc-funding-kw-0802", "doc-stmt-kw-2026-03-dup"} <= cited
