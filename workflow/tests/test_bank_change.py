"""Task 6: bank-detail change path (scenario 7). Held, identity check, controller approval."""

from datetime import UTC, datetime

import pytest

from agents import RealAgents
from contracts.fakes import FakeVerifier
from contracts.models import AlertKind, Event
from rails.guards import policy_check
from workflow import Deps, Workflow
from workflow.approvals import RailsApprovals
from workflow.book import WorkflowBook
from workflow.memrails import MemoryRails
from world import generate


@pytest.fixture
def run():
    rails = MemoryRails()
    approvals = RailsApprovals(rails)
    wf = Workflow(Deps(rails, RealAgents(None), FakeVerifier(), approvals))
    results = wf.replay(generate(scenarios=(7,), n_clean=10).events())
    return rails, approvals, wf, results


def test_request_is_held_with_cited_alert(run):
    rails, approvals, _, results = run
    [r] = [r for r in results if r.route == "bank_change"]
    [alert] = [a for a in WorkflowBook.load(rails).open_alerts()
               if a.draft.kind == AlertKind.bank_detail_change]
    assert alert.draft.invoice_ids == ["INV-KW-0701"] and alert.draft.amount == 4800.0
    assert alert.draft.claims[0].citations[0].source_id == "doc-notice-summit-bank"
    [held] = [e for e in rails.events() if e.type == "bank_change_held"]
    assert held.payload["requires"] == ["identity_verification", "controller_approval"]
    assert held.payload["approval_id"] in approvals.pending()


def test_nothing_changes_bank_details_or_moves_money(run):
    rails, *_ = run
    assert not [e for e in rails.events() if "bank_details_updated" in e.type
                or e.type in ("payment_sent", "money_moved")]
    assert not policy_check("update_bank_details", "workflow", {})[0]
    assert not policy_check("move_money", "controller", {})[0]


def test_agents_cannot_approve_the_change(run):
    _, approvals, *_ = run
    [aid] = approvals.pending()
    with pytest.raises(PermissionError):
        approvals.decide(aid, "case_agent", True)
    assert approvals.decide(aid, "controller.dana", True).approved_by == "controller.dana"


def test_judge_console_style_request(run):
    rails, approvals, wf, _ = run
    when = datetime(2026, 4, 25, 12, tzinfo=UTC)
    ev = Event(id="ev-judge-bank_change-x", type="bank_change_requested", invoice_id="INV-KW-0701",
               valid_from=when, learned_at=when, payload={"vendor_id": "v-summit"})
    [r] = wf.replay([ev])
    assert r.route == "bank_change" and any("held pending approval" in t for t in r.trace)
