"""Task 5b: case. Hold amount is arithmetic; agents only recommend."""

from agents.case import build_case
from contracts.models import (
    Alert,
    AlertDraft,
    AlertKind,
    Citation,
    Claim,
    SourceKind,
    Verdict,
    VerdictStatus,
)


def _alert(inv, amount, kind=AlertKind.unexplained_unpaid, state="open"):
    cite = Citation(source_id=f"doc-{inv}", kind=SourceKind.manager_bank)
    d = AlertDraft(id=f"al-{inv}", kind=kind, manager_id="keyway", invoice_ids=[inv],
                   amount=amount, headline=f"{inv} unpaid",
                   claims=[Claim(text="unpaid", citations=[cite])])
    return Alert(draft=d, verdict=Verdict(alert_id=d.id, status=VerdictStatus.approved,
                                          reasons=[]), state=state)


ALERTS = [_alert("INV-1", 1240.0), _alert("INV-2", 1450.0), _alert("INV-3", 450.0),
          _alert("INV-4", 0.0),                                    # paid late: nothing to hold
          _alert("INV-5", 650.0, AlertKind.paperwork_request),     # vendor-side paperwork
          _alert("INV-6", 999.0, state="withdrawn")]


def test_hold_is_the_sum_still_owed_on_open_unexplained_items():
    c = build_case("keyway", ALERTS)
    assert c.proposed_hold == 3140.00                              # the README's $3,140
    assert c.alerts == ["al-INV-1", "al-INV-2", "al-INV-3"]
    assert {e.source_id for e in c.evidence} == {"doc-INV-1", "doc-INV-2", "doc-INV-3"}
    assert "$3,140.00" in c.summary and c.proposed_by == "case_agent" and c.approved_by is None


def test_no_unexplained_money_no_hold():
    c = build_case("keyway", ALERTS[3:])
    assert c.proposed_hold == 0 and "No hold recommended" in c.summary
