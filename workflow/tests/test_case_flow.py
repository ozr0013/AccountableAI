"""Funding review ("the moment") and controller approvals through the workflow."""

import pytest

from agents import RealAgents
from contracts.fakes import FakeVerifier
from workflow import Deps, Workflow
from workflow.approvals import RailsApprovals
from workflow.memrails import MemoryRails
from workflow.run import funding_event
from world import generate


@pytest.fixture
def setup():
    w = generate(scenarios=(3, 5), n_clean=10)
    rails = MemoryRails()
    approvals = RailsApprovals(rails)
    wf = Workflow(Deps(rails, RealAgents(None), FakeVerifier(), approvals))
    wf.replay(w.events())
    [r] = wf.replay([funding_event("keyway", 18400, w.as_of)])
    return rails, approvals, r


def _proposed(rails):
    [e] = [e for e in rails.events() if e.type == "case_proposed"]
    return e.payload


def test_funding_review_recommends_holding_what_is_still_owed(setup):
    rails, approvals, r = setup
    p = _proposed(rails)
    assert p["case"]["proposed_hold"] == 1240.0 and p["funding_amount"] == 18400.0
    assert p["recommendation"].startswith("Recommend holding $1,240.00 of the $18,400.00")
    assert p["approval_id"] in approvals.pending()


def test_agent_cannot_approve_its_own_proposal(setup):
    rails, approvals, _ = setup
    aid = _proposed(rails)["approval_id"]
    with pytest.raises(PermissionError, match="proposer cannot approve"):
        approvals.decide(aid, "case_agent", True)
    with pytest.raises(PermissionError, match="only the controller"):
        approvals.decide(aid, "chaser", True)
    assert aid in approvals.pending()


def test_controller_approves_or_declines(setup):
    rails, approvals, _ = setup
    aid = _proposed(rails)["approval_id"]
    case = approvals.decide(aid, "controller.dana", True)
    assert case.approved_by == "controller.dana" and approvals.pending() == []
    with pytest.raises(ValueError, match="already decided"):
        approvals.decide(aid, "controller.dana", False)


def test_review_is_replay_safe(setup):
    rails, approvals, _ = setup
    n = len(rails.events())
    wf = Workflow(Deps(rails, RealAgents(None), FakeVerifier(), approvals))
    w = generate(scenarios=(3, 5), n_clean=10)
    wf.replay([funding_event("keyway", 18400, w.as_of)])
    assert len(rails.events()) == n and len(approvals.pending()) == 1
