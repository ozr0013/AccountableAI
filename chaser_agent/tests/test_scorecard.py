"""The scorecard judges planted problems against what is on record."""
from types import SimpleNamespace as NS

from chaser_agent.scorecard import _judge

PAID = [{"amount": 100.0, "on": "2026-05-01"}]


def _line(paid=0.0):
    return NS(paid_amount=paid)


def test_a_clean_invoice_stays_quiet_or_counts_as_a_false_alarm():
    assert _judge("on_time", _line(100), PAID, {"findings": []}, [], False)[0] == "clear"
    noisy = {"findings": [{"code": "late_payment"}]}
    assert _judge("on_time", _line(100), PAID, noisy, [], False)[0] == "false alarm"
    assert _judge("on_time", _line(), [], None, [], False)[0] == "not yet"


def test_unpaid_is_caught_by_chasing_and_missed_when_overdue_without_a_reminder():
    chased = [{"action": "reminder", "level": "polite"}]
    assert _judge("unpaid", _line(), [], {"state": "overdue"}, chased, False)[0] == "caught"
    assert _judge("unpaid", _line(), [], {"state": "overdue"}, [], False)[0] == "missed"
    assert _judge("unpaid", _line(), [], {"state": "monitoring"}, [], False)[0] == "not yet"


def test_payment_problems_need_their_finding():
    case = {"findings": [{"code": "partial_payment"}], "outbox": []}
    assert _judge("partial", _line(50), PAID, case, [], False)[0] == "caught"
    assert _judge("duplicate", _line(100), PAID, case, [], False)[0] == "missed"
    stmt = {"findings": [{"code": "statement_mismatch"}], "outbox": [{"kind": "statement_notice"}]}
    assert _judge("statement_amount", _line(100), PAID, stmt, [], False)[0] == "not yet"
    result, detail = _judge("statement_amount", _line(100), PAID, stmt, [], True)
    assert result == "caught" and "note to Keyway queued" in detail


def test_unfunded_must_not_be_chased():
    assert _judge("unfunded", _line(), [], None, [], False)[0] == "clear"
    assert _judge("unfunded", _line(), [], None, [{"action": "reminder", "level": "polite"}], False)[0] == "false alarm"


def test_a_late_payer_not_paid_yet_is_judged_on_chasing():
    chased = [{"action": "reminder", "level": "polite"}]
    assert _judge("late", _line(), [], {"state": "overdue"}, chased, False)[0] == "caught"
    assert _judge("late", _line(), [], {"state": "overdue"}, [], False)[0] == "missed"
