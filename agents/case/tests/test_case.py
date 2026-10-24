"""Task 5b: case agent. Hold amount is arithmetic; summary is guarded; agents only recommend."""

from agents.case import CaseAgent
from agents.case.agent import CaseSummary
from agents.testing import ScriptedLLM
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
    c = CaseAgent(None).build_case("keyway", ALERTS)
    assert c.proposed_hold == 3140.00                              # the README's $3,140
    assert c.alerts == ["al-INV-1", "al-INV-2", "al-INV-3"]
    assert {e.source_id for e in c.evidence} == {"doc-INV-1", "doc-INV-2", "doc-INV-3"}
    assert "$3,140.00" in c.summary and c.proposed_by == "case_agent" and c.approved_by is None


def test_no_unexplained_money_no_hold():
    c = CaseAgent(None).build_case("keyway", ALERTS[3:])
    assert c.proposed_hold == 0 and "No hold recommended" in c.summary


def test_llm_summary_must_state_the_amount_and_only_recommend():
    bad1 = CaseSummary(summary="Keyway owes several vendors.", cited_source_ids=[])
    bad2 = CaseSummary(summary="We held $3,140.00 from Keyway.", cited_source_ids=["doc-INV-1"])
    good = CaseSummary(summary="Keyway still owes 3 vendors $3,140.00. Recommend holding "
                               "$3,140.00 until payment is shown.", cited_source_ids=["doc-INV-1"])
    llm = ScriptedLLM(bad1, good)
    assert CaseAgent(llm).build_case("keyway", ALERTS).summary == good.summary
    assert "must state the hold amount" in llm.prompts[1]
    llm = ScriptedLLM(bad2, bad2)
    c = CaseAgent(llm).build_case("keyway", ALERTS)
    assert "never say one was placed" in llm.prompts[1] and c.summary.startswith("keyway still owes")


def test_llm_cannot_change_the_hold_amount():
    llm = ScriptedLLM(CaseSummary(summary="Recommend holding $3,140.00.", cited_source_ids=[]))
    assert CaseAgent(llm).build_case("keyway", ALERTS).proposed_hold == 3140.00
