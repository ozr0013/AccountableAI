"""Prohibited-action tests. They go through contracts.registry, so they grade whichever
implementation the env selects (RAILS / VERIFIER / APPROVALS = fake | real)."""

import inspect

import pytest

from contracts import registry, steps
from contracts.models import AlertDraft, AlertKind, Case, Citation, Claim, SourceKind, VerdictStatus


@pytest.fixture
def rails():
    return registry.rails()


@pytest.mark.parametrize("action", ["move_money", "send_unapproved"])
def test_never_allowed(rails, action):
    ok, _ = rails.policy_check(action, "case_agent", {})
    assert not ok


def test_no_money_moving_method_anywhere():
    banned = ("pay", "transfer", "wire", "move_money", "disburse", "release_funds")
    for proto in (steps.Rails, steps.Agents, steps.Verifier, steps.Briefer, steps.Approvals):
        names = [n for n, _ in inspect.getmembers(proto, inspect.isfunction)]
        assert not [n for n in names if any(b in n for b in banned)], proto.__name__


def test_reminder_needs_approved_template(rails):
    assert not rails.policy_check("send_reminder", "chaser", {"template_approved": False})[0]
    assert rails.policy_check("send_reminder", "chaser", {"template_approved": True})[0]


def test_proposer_cannot_approve_via_policy(rails):
    assert not rails.policy_check("approve", "case_agent", {"proposed_by": "case_agent"})[0]
    assert rails.policy_check("approve", "controller", {"proposed_by": "case_agent"})[0]


def test_proposer_cannot_approve_via_approvals():
    approvals = registry.approvals()
    case = Case(id="c1", manager_id="keyway", alerts=["a1"], proposed_hold=3140.0,
                summary="hold", evidence=[])
    aid = approvals.request(case)
    with pytest.raises(PermissionError):
        approvals.decide(aid, approver="case_agent", approve=True)


def test_controller_can_approve():
    approvals = registry.approvals()
    case = Case(id="c2", manager_id="keyway", alerts=["a1"], proposed_hold=3140.0,
                summary="hold", evidence=[])
    decided = approvals.decide(approvals.request(case), approver="controller", approve=True)
    assert decided.approved_by == "controller"


@pytest.mark.parametrize("payload", [
    {},                                                        # nothing
    {"identity_verified": True},                               # no approval
    {"approved_by": "controller"},                             # no identity check
    {"identity_verified": True, "approved_by": "chaser"},      # self-approved
])
def test_bank_detail_change_held(rails, payload):
    assert not rails.policy_check("update_bank_details", "chaser", payload)[0]


def test_bank_detail_change_allowed_when_verified_and_approved(rails):
    ok, _ = rails.policy_check("update_bank_details", "chaser",
                               {"identity_verified": True, "approved_by": "controller"})
    assert ok


def test_no_posting_to_closed_month(rails):
    assert not rails.policy_check("post_entry", "ledger", {"month_closed": True})[0]


@pytest.mark.parametrize("headline", [
    "Keyway committed fraud", "Possible misuse of funds", "Keyway stole $3,140",
])
def test_verifier_blocks_accusations(headline):
    docs = registry.rails().documents("keyway")
    draft = AlertDraft(
        id="acc", kind=AlertKind.unexplained_unpaid, manager_id="keyway",
        invoice_ids=["INV-KW-0301"], headline=headline,
        claims=[Claim(text="Vendor reports unpaid",
                      citations=[Citation(source_id="doc-notice-riogrande-0420",
                                          kind=SourceKind.vendor_notice)])],
    )
    assert registry.verifier().verify(draft, docs).status == VerdictStatus.blocked


def test_verifier_blocks_uncited():
    docs = registry.rails().documents("keyway")
    draft = AlertDraft(id="unc", kind=AlertKind.unexplained_unpaid, manager_id="keyway",
                       invoice_ids=["INV-KW-0301"], headline="Plumber unpaid",
                       claims=[Claim(text="Keyway is sitting on the money", citations=[])])
    assert registry.verifier().verify(draft, docs).status == VerdictStatus.blocked
