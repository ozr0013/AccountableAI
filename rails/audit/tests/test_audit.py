from datetime import date

import pytest

from agents.intake.engine import stamp
from contracts.models import Citation, Event, SourceKind
from rails.audit import Audit, InvariantViolation
from rails.audit.regression import replay
from workflow.memrails import MemoryRails

DAY = date(2026, 3, 20)
SOURCE = Citation(source_id="bank-proof", kind=SourceKind.manager_bank, quote="2026-03-15 paid $100")


@pytest.mark.parametrize("target,predicted,actual", [("alert", True, False), ("alert", False, True), ("cause", "unexplained", "vendor_side")])
def test_outcome_catches_alert_no_alert_and_cause_and_exports_replay(tmp_path, target, predicted, actual):
    rails = MemoryRails()
    audit = Audit(rails, tmp_path)
    audit.predict("INV-1", target, predicted, DAY, [SOURCE])
    audit.settle("INV-1", {target: actual}, date(2026, 3, 15), date(2026, 3, 22), SOURCE)
    mistakes = [e for e in rails.events() if e.type == "mistake_detected"]
    assert len(mistakes) == 1
    correction = next(e for e in rails.events() if e.type == "correction_recorded")
    assert correction.payload["value"] == actual
    assert correction.payload["affected"] == ["INV-1"]
    assert replay(next(tmp_path.glob("*.json")))
    before = len(rails.events())
    audit.settle("INV-1", {target: actual}, date(2026, 3, 15), date(2026, 3, 22), SOURCE)
    assert len(rails.events()) == before


def test_later_payment_is_not_a_mistake():
    rails = MemoryRails()
    audit = Audit(rails)
    audit.predict("INV-1", "alert", True, DAY, [SOURCE])
    audit.settle("INV-1", {"alert": False}, date(2026, 3, 21), date(2026, 3, 22), SOURCE)
    assert not any(e.type == "mistake_detected" for e in rails.events())


def test_disagreement_escalates_and_human_override_is_labelled():
    rails = MemoryRails()
    audit = Audit(rails)
    assert audit.disagreement("INV-1", "vendor_side", "unexplained", DAY, SOURCE)
    audit.override("INV-1", {"alert": False}, DAY, SOURCE, "controller.dana")
    assert {e.payload["signal"] for e in rails.events() if e.type == "mistake_detected"} == {"disagreement", "human_override"}
    with pytest.raises(ValueError, match="controller"):
        audit.override("INV-1", {}, DAY, SOURCE, "agent")


def test_duplicate_action_and_illegal_transition_halt_and_propose_human_rule_change(tmp_path):
    rails = MemoryRails()
    audit = Audit(rails, tmp_path)
    for i in (1, 2):
        rails.append_event(Event(id=str(i), type="action_issued", valid_from=stamp(DAY), learned_at=stamp(DAY),
                                 payload={"action_key": "same", "action": "send_chase"}, source=SOURCE))
    with pytest.raises(InvariantViolation, match="duplicate_action"):
        audit.check(DAY)
    assert Audit(rails).halted
    assert any(e.type == "rule_change_proposed" and e.payload["status"] == "awaiting_human" for e in rails.events())
    other = MemoryRails()
    a = Audit(other)
    a.emit("alert_transition", "bad", DAY, "INV-1", {"state": "resolved"}, SOURCE)
    with pytest.raises(InvariantViolation, match="illegal_transition"):
        a.check(DAY)


def test_autonomy_requires_real_settled_record_and_cannot_expand_money_actions():
    rails = MemoryRails()
    audit = Audit(rails)
    assert not audit.autonomy("reminder")["expanded"]
    for k in range(100):
        audit.emit("prediction_settled", k, DAY, payload={"prediction_id": str(k), "action_type": "reminder", "correct": True}, source=SOURCE)
    assert audit.autonomy("reminder")["expanded"]
    assert not audit.autonomy("pay_vendor")["expanded"]
