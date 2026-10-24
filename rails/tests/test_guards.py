import ast
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from contracts.models import Document, Event, SourceKind
from rails.events.ingest import document_event
from rails.guards import (APPROVED_TEMPLATES, accounting_check, banned_terms_in, draft_digest,
                          policy_check, render)
from world import generate

ROOT = Path(__file__).resolve().parents[2]


def ok(*a):
    return policy_check(*a)[0]


# -- no money tool ------------------------------------------------------------------------

MONEY_FN = re.compile(r"^(move_money|send_money|transfer\w*|wire\w*|pay_vendor|pay|release_funds|"
                      r"disburse\w*|initiate_payment|make_payment)$")


def test_no_money_moving_function_exists_in_rails_or_world():
    found = []
    for folder in ("rails", "world"):
        for f in (ROOT / folder).rglob("*.py"):
            for n in ast.walk(ast.parse(f.read_text())):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and MONEY_FN.match(n.name):
                    found.append(f"{f.relative_to(ROOT)}:{n.name}")
    assert found == []


@pytest.mark.parametrize("action", ["move_money", "pay_vendor", "transfer", "wire", "release_funds",
                                    "send_unapproved"])
def test_money_actions_are_refused_for_everyone(action):
    for actor, role in (("agent", "agent"), ("controller", "controller")):
        allowed, why = policy_check(action, actor, {"actor_role": role})
        assert not allowed and "never" in why


def test_unknown_actions_fail_closed():
    allowed, why = policy_check("delete_history", "agent", {})
    assert not allowed and "unknown action" in why


# -- approvals and segregation of duties --------------------------------------------------

def test_proposer_cannot_approve_and_agents_cannot_approve_or_hold():
    assert not ok("approve", "kanishka", {"proposed_by": "kanishka", "actor_role": "controller"})
    assert not ok("approve", "case_agent", {"proposed_by": "x", "actor_role": "agent"})
    assert ok("approve", "controller-1", {"proposed_by": "case_agent", "actor_role": "controller"})
    assert not ok("hold", "case_agent", {"actor_role": "agent"})
    assert ok("hold", "controller-1", {"actor_role": "controller"})
    assert ok("propose_hold", "case_agent", {"actor_role": "agent"})


# -- outbound -----------------------------------------------------------------------------

def test_reminders_only_from_approved_templates_on_schedule():
    assert ok("send_chase", "chaser", {"template_id": "tpl-polite"})
    assert not ok("send_chase", "chaser", {"template_id": "made-up"})
    assert not ok("send_chase", "chaser", {"template_id": "tpl-firm"})                 # skipped polite
    assert not ok("send_chase", "chaser", {"template_id": "tpl-firm",
                                           "prior_levels": ["polite"], "days_since_last": 2})
    assert ok("send_chase", "chaser", {"template_id": "tpl-firm",
                                       "prior_levels": ["polite"], "days_since_last": 5})
    assert not ok("send_chase", "chaser", {"template_id": "tpl-polite",
                                           "prior_levels": ["polite"], "days_since_last": 9})  # repeat
    assert ok("send_chase", "chaser", {"template_id": "tpl-escalation",
                                       "prior_levels": ["polite", "firm"], "days_since_last": 6})


def test_any_other_outbound_needs_approval_of_that_exact_draft():
    base = {"to": "keyway@example.com", "body": "Please send your W-9.", "proposed_by": "case_agent"}
    assert not ok("send_outbound", "case_agent", base)
    good = {**base, "approval": {"draft_digest": draft_digest(base["to"], base["body"]),
                                 "approved_by": "controller-1"}}
    assert ok("send_outbound", "case_agent", good)
    assert not ok("send_outbound", "case_agent", {**good, "body": "Please send your W-9 now."})
    self_approved = {**good, "approval": {**good["approval"], "approved_by": "case_agent"}}
    assert not ok("send_outbound", "case_agent", self_approved)
    accusing = {**base, "body": "This looks like fraud."}
    accusing["approval"] = {"draft_digest": draft_digest(accusing["to"], accusing["body"]),
                            "approved_by": "controller-1"}
    assert not ok("send_outbound", "case_agent", accusing)


def test_templates_never_accuse_and_render():
    assert all(not banned_terms_in(t["body"]) for t in APPROVED_TEMPLATES.values())
    text = render("tpl-escalation", manager_name="Keyway", amount="$1,240.00",
                  vendor_name="Rio Grande Plumbing", invoice_ref="KW-0301")
    assert "unexplained" in text and "fraud" not in text.lower()


# -- alerts and bank details --------------------------------------------------------------

def test_no_alert_without_citation_or_with_accusation():
    assert not ok("create_alert", "cause_agent", {"citations": [], "text": "Unpaid."})
    assert not ok("create_alert", "cause_agent", {"citations": ["c"], "text": "Possible misuse of funds"})
    assert ok("create_alert", "cause_agent", {"citations": ["c"], "text": "Unexplained unpaid invoice."})


def test_bank_detail_change_is_held_until_verified_and_approved():       # scenario 7
    assert policy_check("request_bank_change", "vendor_portal", {})[0]
    assert not ok("update_bank_details", "ops", {"requested_by": "v"})
    assert not ok("update_bank_details", "ops", {"identity_verified": True, "requested_by": "v"})
    assert not ok("update_bank_details", "ops", {"identity_verified": True, "requested_by": "v",
                                                 "approved_by": "v"})
    assert ok("update_bank_details", "ops", {"identity_verified": True, "requested_by": "v",
                                             "approved_by": "controller-1"})


# -- accounting ---------------------------------------------------------------------------

def test_clean_world_books_agree():
    assert accounting_check(generate().events()) == []


def test_unaccounted_money_and_replay_duplicates_and_closed_months_are_reported():
    evs = generate(n_clean=2).events()
    dup = next(e for e in evs if e.type == "document_ingested" and e.payload["kind"] == "funding")
    evs.append(dup.model_copy(update={"id": "ev-replayed", "payload": {**dup.payload, "id": "other"}}))
    kinds = {i.check for i in accounting_check(evs)}
    assert "replay_duplicate" in kinds and "balanced" in kinds        # funded twice, nothing explains it

    ts = datetime(2026, 3, 31, 23, tzinfo=timezone.utc)
    evs = generate(n_clean=0).events() + [
        Event(id="close-mar", type="period_closed", valid_from=ts, learned_at=ts,
              payload={"month": "2026-03"})]
    late = Document(id="doc-late-funding", kind=SourceKind.funding, manager_id="keyway",
                    dated=date(2026, 3, 20), data={"invoice_id": "INV-KW-0301", "amount": 5.0})
    evs.append(document_event(late, learned_at=datetime(2026, 4, 25, tzinfo=timezone.utc)))
    assert "closed_month" in {i.check for i in accounting_check(evs)}
