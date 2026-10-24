"""The workflow with Kanishka's real agents (rules mode, no API key) over all 9 scenarios."""

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
    assert "INV-SB-0101" not in alerts                                   # scenario 1
    assert alerts["INV-PT-0601"].draft.kind == AlertKind.owner_pending_approval   # scenario 6


def test_paperwork_gets_a_document_request_not_a_manager_alert(outcome):
    alerts, _ = outcome
    assert alerts["INV-PT-0201"].draft.kind == AlertKind.paperwork_request        # scenario 2


def test_unexplained_items_alert_and_get_chased(outcome):
    alerts, book = outcome
    for inv in ("INV-KW-0301", "INV-PT-0901"):                          # scenarios 3, 9
        assert alerts[inv].draft.kind == AlertKind.unexplained_unpaid
    s3 = {c.source_id for cl in alerts["INV-KW-0301"].draft.claims for c in cl.citations}
    assert {"doc-stmt-kw-2026-03", "doc-bank-kw-2026-03", "doc-notice-riogrande-0420"} <= s3
    assert book.chases["keyway"] and book.chases["peachtree"]


def test_scenario_7_held_payment_is_not_blamed_on_the_manager(outcome):
    alerts, _ = outcome
    assert alerts["INV-SB-0701"].draft.kind == AlertKind.bank_detail_change


def test_paid_late_electrician_alerted_before_the_moment(outcome):
    alerts, _ = outcome
    assert alerts["INV-KW-0402"].draft.kind == AlertKind.unexplained_unpaid      # scenario 5 pre-fact


def test_scenario_4_one_pattern_alert_for_sunbelt_only(outcome):
    _, book = outcome
    patterns = [a for a in book.open_alerts() if a.draft.kind == AlertKind.recurring_pattern]
    assert [a.draft.manager_id for a in patterns] == ["sunbelt"]
    [p] = patterns
    assert "4 of 6 months" in p.draft.headline
    kinds = {c.kind for cl in p.draft.claims for c in cl.citations}
    assert {"manager_bank", "agreement"} <= kinds
    # Sunbelt's individual late-but-paid invoices are folded into the pattern
    assert not [a for a in book.open_alerts("sunbelt")
                if a.draft.kind == AlertKind.unexplained_unpaid and a.draft.amount == 0]


def test_scenario_8_duplicate_billing_cites_both_fundings(outcome):
    alerts, _ = outcome
    d = alerts["INV-SB-0802"]
    assert d.draft.kind == AlertKind.duplicate_billing and d.draft.amount == 320.0
    cited = {c.source_id for cl in d.draft.claims for c in cl.citations}
    assert {"doc-funding-sb-0801", "doc-funding-sb-0802", "doc-stmt-sb-2026-03-dup"} <= cited
