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
    return {i: a for a in book.open_alerts() for i in a.draft.invoice_ids}, book


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


def test_paid_late_electrician_alerted_before_the_moment(outcome):
    alerts, _ = outcome
    assert alerts["INV-KW-0402"].draft.kind == AlertKind.unexplained_unpaid      # scenario 5 pre-fact
