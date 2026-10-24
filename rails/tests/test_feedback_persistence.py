from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from contracts.models import Document, LedgerLine
from rails.events.ingest import document_event
from rails.real import RealRails
from workflow.autonomy_api import create_app
from workflow.cases import CaseStore
from workflow.feedback import FeedbackStore
from workflow.human import Principal

NOW = datetime(2026, 10, 24, tzinfo=UTC)


def test_feedback_promotions_recover_from_second_connection(schema):
    rails = RealRails(schema=schema)
    cases = CaseStore(rails)
    rails.append_event(
        document_event(
            Document(
                id="invoice",
                kind="vendor_invoice",
                manager_id="m",
                vendor_id="v",
                dated=NOW.date(),
                data={"invoice_id": "i", "amount": 1, "received": NOW.date().isoformat()},
            ),
            NOW,
        )
    )
    case = cases.ensure(LedgerLine(invoice_id="i", manager_id="m", vendor_id="v", amount=1), NOW)
    decision = cases.record(case.id, "supervisor_tool_result", {"result": "wait"}, NOW, "decision")
    store = FeedbackStore(rails)
    feedback = store.feedback(case.id, decision.id, "Cite source", NOW, "f")
    candidate = store.candidate(
        feedback["id"], "case-investigation", "2", "Cite source", ["read"], NOW, "c"
    )
    store.run_evaluation(candidate["id"], case_ids=[case.id], now=NOW, key="e")
    principal = Principal("controller.dana", "controller")
    store.promote(candidate["id"], principal, NOW, "p")
    restarted = FeedbackStore(RealRails(schema=schema))
    assert restarted.active_override("case-investigation")["version"] == "2"
    restarted.rollback("case-investigation", principal, NOW, "r")
    assert store.active_override("case-investigation") is None
    with pytest.raises(Exception, match="append-only"):
        rails.store.conn.execute("DELETE FROM events WHERE type='skill_promotion'")


def test_api_approved_draft_and_job_persist_together(schema, monkeypatch):
    from workflow.human import HumanApprovals
    from workflow.outbox import Outbox
    from workflow.scheduler import Scheduler

    rails = RealRails(schema=schema)
    case = CaseStore(rails).ensure(
        LedgerLine(invoice_id="i", manager_id="m", vendor_id="v", amount=1), NOW
    )
    ticket = HumanApprovals(rails).request(
        case,
        "send_message",
        {"recipient": "management:m", "body": "Please send proof"},
        NOW,
        "draft",
    )
    monkeypatch.setenv("CONTROLLER_API_TOKEN", "test-private")
    client = TestClient(create_app(rails, clock=lambda: NOW))
    response = client.post(
        f"/autonomy/approvals/{ticket['id']}/decide",
        headers={"Authorization": "Bearer test-private"},
        json={"approve": True, "digest": ticket["digest"]},
    )
    assert response.status_code == 200, response.text
    other = RealRails(schema=schema)
    assert len(Outbox(other).list()) == 1
    assert len(Scheduler(other).list()) == 1


def test_approval_api_rolls_back_ticket_and_outbox_if_job_write_fails(schema, monkeypatch):
    from workflow.human import HumanApprovals
    from workflow.outbox import Outbox
    from workflow.scheduler import Scheduler

    rails = RealRails(schema=schema)
    case = CaseStore(rails).ensure(
        LedgerLine(invoice_id="i", manager_id="m", vendor_id="v", amount=1), NOW
    )
    ticket = HumanApprovals(rails).request(
        case, "send_message", {"recipient": "management:m", "body": "Proof please"}, NOW, "draft"
    )
    monkeypatch.setenv("CONTROLLER_API_TOKEN", "test-private")

    def fail_schedule(*args, **kwargs):
        raise RuntimeError("simulated job persistence failure")

    monkeypatch.setattr(Scheduler, "schedule", fail_schedule)
    client = TestClient(create_app(rails, clock=lambda: NOW))
    with pytest.raises(RuntimeError, match="simulated"):
        client.post(
            f"/autonomy/approvals/{ticket['id']}/decide",
            headers={"Authorization": "Bearer test-private"},
            json={"approve": True, "digest": ticket["digest"]},
        )
    assert HumanApprovals(rails).get(ticket["id"])["status"] == "pending"
    assert Outbox(rails).list() == []
