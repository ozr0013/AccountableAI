"""The chaser's daily decisions: agreement deadline and cadence, approvals, escalation, hand-off."""
from datetime import date

from chaser_agent.daily import add_days, decide, norm_name
from contracts.models import LedgerLine

DEADLINE = {"rule_id": "doc:pay_within_days", "rule_type": "pay_within_days", "status": "active",
            "params": {"days": 30, "basis": "calendar"}, "check": "Pay within 30 days",
            "source_quote": "Within 30 days of receipt"}
RULES = [DEADLINE]


def _line(paid=0.0):
    return LedgerLine(invoice_id="INV-KW-0301", manager_id="keyway", vendor_id="v-riogrande",
                      amount=1240.0, funded_amount=1240.0, invoice_received=date(2026, 3, 3), paid_amount=paid,
                      paid_on=date(2026, 3, 20) if paid else None)


def _decide(today, history=(), rules=RULES, paid=0.0):
    return decide(_line(paid), "Rio Grande Plumbing", "Keyway", rules, list(history), today)


def _row(level, on, status, action="reminder"):
    return {"action": action, "level": level, "as_of": on, "status": status,
            "action_id": f"INV-KW-0301:{action}:{level}:{on}"}


def test_deadline_comes_from_the_agreement_rule():
    d = _decide(date(2026, 3, 30))
    assert d.action == "not_due" and d.due_date == "2026-04-02" and d.rule_id == "doc:pay_within_days"


def test_polite_reminder_is_queued_without_approval():
    d = _decide(date(2026, 4, 22))
    assert d.action == "reminder" and d.level == "polite" and d.status == "queued"
    assert "INV-KW-0301" in d.message and d.rule_quote == "Within 30 days of receipt"


def test_firm_reminder_needs_the_controller_and_blocks_until_decided():
    h = [_row("polite", "2026-04-22", "queued")]
    firm = _decide(date(2026, 4, 27), h)
    assert firm.level == "firm" and firm.status == "needs_approval"
    h.append(_row("firm", "2026-04-27", "needs_approval"))
    assert "awaiting controller approval" in _decide(date(2026, 5, 10), h).reason


def test_declined_reminder_is_redrafted_after_one_cadence():
    h = [_row("polite", "2026-04-22", "queued"), _row("firm", "2026-04-27", "rejected")]
    assert _decide(date(2026, 4, 29), h).action == "wait"
    again = _decide(date(2026, 5, 2), h)
    assert again.level == "firm" and again.attempt == 1


def test_cadence_comes_from_the_agreement_with_a_five_day_floor():
    weekly = RULES + [{"rule_id": "doc:reminder_cadence", "rule_type": "reminder_cadence",
                       "status": "active", "params": {"days": 7}, "check": "",
                       "source_quote": "reminder every 7 days"}]
    h = [_row("polite", "2026-04-22", "queued")]
    assert _decide(date(2026, 4, 28), h, weekly).action == "wait"
    assert _decide(date(2026, 4, 29), h, weekly).level == "firm"
    fast = RULES + [{**weekly[-1], "params": {"days": 2}}]
    assert "approved floor is 5" in _decide(date(2026, 4, 25), h, fast).reason


def test_escalation_notifies_both_proposes_a_hold_and_needs_the_controller():
    h = [_row("polite", "2026-04-22", "queued"), _row("firm", "2026-04-27", "approved"),
         _row("escalation", "2026-05-02", "approved")]
    assert _decide(date(2026, 5, 4), h).action == "wait"
    d = _decide(date(2026, 5, 7), h)
    assert d.action == "escalate" and d.status == "needs_controller"
    assert "Oakline (owner)" in d.message and "hold $1,240.00" in d.message
    h.append(_row(None, "2026-05-07", "needs_controller", action="escalate"))
    assert _decide(date(2026, 5, 8), h).action == "wait"


def test_any_payment_hands_off_to_the_anomaly_agent():
    assert _decide(date(2026, 4, 22), paid=1240.0).action == "handoff_anomaly"
    partial = _decide(date(2026, 4, 22), paid=1000.0)
    assert partial.action == "handoff_anomaly" and "$240.00 short" in partial.reason


def test_agreement_waiting_for_controller_is_held_not_chased():
    pending = [{**r, "status": "pending_controller"} for r in RULES]
    assert _decide(date(2026, 4, 22), rules=pending).action == "hold"


def test_no_agreement_is_skipped():
    assert _decide(date(2026, 4, 22), rules=[]).action == "skip"


def test_business_days_skip_weekends_and_names_match_without_suffixes():
    assert add_days(date(2026, 4, 24), 2, "business") == date(2026, 4, 28)
    assert norm_name("Rio Grande Plumbing LLC") == norm_name("Rio Grande Plumbing")


def test_vendor_without_agreement_uses_the_management_agreement_deadline_only():
    from chaser_agent.daily import _rules_by_vendor, rules_for
    rows = [{**DEADLINE, "vendor_name": "Keyway Property Management", "params": {"days": 10}},
            {"rule_id": "m:payee", "rule_type": "payee", "status": "active", "params": {},
             "check": "name Keyway", "vendor_name": "Keyway Property Management"},
            {**DEADLINE, "vendor_name": "Rio Grande Plumbing LLC"}]
    by_vendor = _rules_by_vendor(rows)
    assert rules_for(by_vendor, "Rio Grande Plumbing", "Keyway Property Management")[0]["params"]["days"] == 30
    fallback = rules_for(by_vendor, "Topline Roofing", "Keyway Property Management")
    assert [r["rule_type"] for r in fallback] == ["pay_within_days"]
    assert fallback[0]["params"]["days"] == 10


def test_unfunded_invoice_is_not_chased():
    line = LedgerLine(invoice_id="INV-KW-0601", manager_id="keyway", vendor_id="v-x", amount=8400.0,
                      invoice_received=date(2026, 3, 3))
    d = decide(line, "Rio Grande Plumbing", "Keyway", RULES, [], date(2026, 4, 22))
    assert d.action == "skip" and "not funded" in d.reason


def test_all_levels_and_the_escalation_notice_go_out_without_approval(monkeypatch):
    from chaser_agent import daily
    monkeypatch.setattr(daily, "AUTO_LEVELS", {"polite", "firm", "escalation"})
    monkeypatch.setattr(daily, "AUTO_ESCALATE", True)
    firm = _decide(date(2026, 4, 27), [_row("polite", "2026-04-22", "queued")])
    assert (firm.level, firm.status) == ("firm", "queued")
    sent = [_row("polite", "2026-04-22", "queued"), _row("firm", "2026-04-27", "queued"),
            _row("escalation", "2026-05-02", "queued")]
    raised = _decide(date(2026, 5, 7), sent)
    assert raised.action == "escalate" and raised.status == "queued"
