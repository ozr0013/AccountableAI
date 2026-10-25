"""The chaser's hand-off row is marked reviewed once the anomaly checks give a verdict."""
from chaser_agent.api import mark_handoffs_reviewed


class Mirror:
    def __init__(self, actions):
        self.actions, self.decisions = actions, []

    def query(self, sql):
        if "chase_decision" in sql:
            return [{"action_id": d[0], "status": d[1], "decided_by": d[2],
                     "note": d[3].get("some")} for d in self.decisions]
        return self.actions

    def call(self, reducer, args):
        assert reducer == "decide_chase"
        self.decisions.append(args)


HANDOFF = {"action_id": "INV-KW-0301:handoff_anomaly:handoff_anomaly:0", "invoice_id": "INV-KW-0301",
           "action": "handoff_anomaly", "status": "recorded"}


def test_paid_case_marks_the_handoff_reviewed_with_the_findings():
    m = Mirror([HANDOFF])
    case = {"invoice_id": "INV-KW-0301", "state": "needs_controller",
            "findings": [{"code": "late_payment"}, {"code": "missing_payment_identity"}]}
    assert mark_handoffs_reviewed(m, [case]) == 1
    [args] = m.decisions
    assert args[1] == "reviewed" and args[2] == "anomaly_checks"
    assert args[3] == {"some": "case needs_controller: late_payment, missing_payment_identity"}
    assert mark_handoffs_reviewed(m, [case]) == 0          # reviewed once, never again


def test_handoff_waits_while_the_case_has_no_payment_verdict():
    m = Mirror([HANDOFF])
    assert mark_handoffs_reviewed(m, [{"invoice_id": "INV-KW-0301", "state": "overdue",
                                       "findings": []}]) == 0
    assert not m.decisions


def test_undecided_reminder_draft_is_cancelled_once_payment_arrives():
    draft = {"action_id": "INV-KW-0301:reminder:firm:0", "invoice_id": "INV-KW-0301",
             "action": "reminder", "status": "needs_approval"}
    m = Mirror([draft, HANDOFF])
    mark_handoffs_reviewed(m, [{"invoice_id": "INV-KW-0301", "state": "needs_controller",
                                "findings": [{"code": "late_payment"}]}])
    assert [(d[0], d[1]) for d in m.decisions] == [
        ("INV-KW-0301:reminder:firm:0", "cancelled"),
        ("INV-KW-0301:handoff_anomaly:handoff_anomaly:0", "reviewed")]
