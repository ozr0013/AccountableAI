"""The chaser's daily decisions: deadline from the agreement rule, ladder, hand-off, holds."""
from datetime import date

from chaser_agent.daily import add_days, decide, norm_name
from contracts.models import LedgerLine

RULES = [{"rule_id": "doc:pay_within_days", "rule_type": "pay_within_days", "status": "active",
          "params": {"days": 30, "basis": "calendar"}, "check": "Pay within 30 days",
          "source_quote": "Within 30 days of receipt"},
         {"rule_id": "doc:payee", "rule_type": "payee", "status": "active",
          "params": {"vendor": "Rio Grande Plumbing LLC"}, "check": "", "source_quote": "x"}]


def _line(paid=0.0):
    return LedgerLine(invoice_id="INV-KW-0301", manager_id="keyway", vendor_id="v-riogrande",
                      amount=1240.0, invoice_received=date(2026, 3, 3), paid_amount=paid,
                      paid_on=date(2026, 3, 20) if paid else None)


def _decide(today, history=(), rules=RULES, paid=0.0):
    return decide(_line(paid), "Rio Grande Plumbing", "Keyway", rules, list(history), today)


def _sent(level, on):
    return {"action": "reminder", "level": level, "as_of": on}


def test_deadline_comes_from_the_agreement_rule():
    d = _decide(date(2026, 3, 30))
    assert d.action == "not_due" and d.due_date == "2026-04-02" and d.rule_id == "doc:pay_within_days"


def test_overdue_gets_the_polite_reminder_citing_the_rule():
    d = _decide(date(2026, 4, 22))
    assert d.action == "reminder" and d.level == "polite" and d.days_overdue == 20
    assert "INV-KW-0301" in d.message and d.rule_quote == "Within 30 days of receipt"


def test_ladder_climbs_five_days_apart_then_escalates_once():
    h = [_sent("polite", "2026-04-22")]
    assert _decide(date(2026, 4, 24), h).action == "wait"
    assert _decide(date(2026, 4, 27), h).level == "firm"
    h += [_sent("firm", "2026-04-27"), _sent("escalation", "2026-05-02")]
    assert _decide(date(2026, 5, 3), h).action == "escalate"
    h += [{"action": "escalate", "level": None, "as_of": "2026-05-03"}]
    assert _decide(date(2026, 5, 4), h).action == "wait"


def test_paid_in_full_hands_off_to_the_anomaly_agent():
    assert _decide(date(2026, 4, 22), paid=1240.0).action == "handoff_anomaly"


def test_partial_payment_chases_what_is_still_owed():
    d = _decide(date(2026, 4, 22), paid=1000.0)
    assert d.action == "reminder" and d.amount_owed == 240.0 and "$240.00" in d.message


def test_agreement_waiting_for_controller_is_held_not_chased():
    pending = [{**r, "status": "pending_controller"} for r in RULES]
    assert _decide(date(2026, 4, 22), rules=pending).action == "hold"


def test_no_agreement_is_skipped():
    assert _decide(date(2026, 4, 22), rules=[]).action == "skip"


def test_business_days_skip_weekends_and_names_match_without_suffixes():
    assert add_days(date(2026, 4, 24), 2, "business") == date(2026, 4, 28)   # Fri + 2 -> Tue
    assert norm_name("Rio Grande Plumbing LLC") == norm_name("Rio Grande Plumbing")
