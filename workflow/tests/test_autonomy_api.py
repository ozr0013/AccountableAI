from datetime import UTC, datetime

from fastapi.testclient import TestClient

from workflow.autonomy_api import create_app
from workflow.cases import CaseStore
from workflow.human import HumanApprovals
from workflow.memrails import MemoryRails

NOW = datetime(2026, 4, 22, 12, tzinfo=UTC)


def setup(monkeypatch):
    monkeypatch.setenv("CONTROLLER_API_TOKEN", "private-test-token")
    rails = MemoryRails.from_world((3,), n_clean=0)
    case = CaseStore(rails).ensure(rails.ledger("keyway")[0], NOW)
    return rails, case, TestClient(create_app(rails, clock=lambda: NOW))


def test_auth_required_configured_and_fixed_principal(monkeypatch):
    _rails, case, client = setup(monkeypatch)
    assert client.get("/autonomy/cases").status_code == 401
    assert (
        client.get("/autonomy/cases", headers={"Authorization": "Bearer wrong"}).status_code == 401
    )
    headers = {"Authorization": "Bearer private-test-token"}
    assert client.get("/autonomy/cases", headers=headers).json()[0]["id"] == case.id
    assert client.get("/autonomy/cases/missing", headers=headers).status_code == 404
    monkeypatch.delenv("CONTROLLER_API_TOKEN")
    assert client.get("/autonomy/cases", headers=headers).status_code == 503


def test_approval_queues_exact_draft_and_wake_without_model(monkeypatch):
    rails, case, client = setup(monkeypatch)
    headers = {"Authorization": "Bearer private-test-token"}
    ticket = HumanApprovals(rails).request(
        case,
        "send_message",
        {"recipient": "management:keyway", "body": "Statement please"},
        NOW,
        "draft",
    )
    response = client.post(
        f"/autonomy/approvals/{ticket['id']}/decide",
        headers=headers,
        json={"approve": True, "digest": ticket["digest"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["decided_by"] == "controller.dana"
    assert len(client.get("/autonomy/outbox", headers=headers).json()) == 1
    assert len(client.get("/autonomy/jobs", headers=headers).json()) == 1
    assert (
        client.post(
            f"/autonomy/approvals/{ticket['id']}/decide",
            headers=headers,
            json={"approve": True, "digest": ticket["digest"]},
        ).status_code
        == 200
    )
    assert len(client.get("/autonomy/outbox", headers=headers).json()) == 1
    response = client.post(
        f"/autonomy/approvals/{ticket['id']}/decide",
        headers=headers,
        json={"approve": True, "digest": ticket["digest"], "approver": "agent"},
    )
    assert response.status_code == 422


def test_feedback_endpoints_and_capability_boundary(monkeypatch):
    rails, case, client = setup(monkeypatch)
    headers = {"Authorization": "Bearer private-test-token"}
    decision = CaseStore(rails).record(
        case.id, "supervisor_tool_result", {"result": "wait"}, NOW, "decision"
    )
    feedback = client.post(
        "/autonomy/feedback",
        headers=headers,
        json={"case_id": case.id, "decision_id": decision.id, "text": "Cite source", "key": "f"},
    )
    assert feedback.status_code == 200
    candidate_body = {
        "feedback_id": feedback.json()["id"],
        "skill_name": "case-investigation",
        "version": "2",
        "text": "Cite source",
        "capabilities": ["read"],
        "key": "c",
    }
    assert (
        client.post(
            "/autonomy/skills/candidates",
            headers=headers,
            json={**candidate_body, "capabilities": ["shell"]},
        ).status_code
        == 422
    )
    candidate = client.post(
        "/autonomy/skills/candidates", headers=headers, json=candidate_body
    ).json()
    base = f"/autonomy/skills/candidates/{candidate['id']}"
    assert client.post(base + "/promote", headers=headers, json={"key": "p"}).status_code == 422
    assert (
        client.post(
            base + "/evaluations",
            headers=headers,
            json={"case_ids": [case.id], "key": "e"},
        ).status_code
        == 200
    )
    assert client.post(base + "/promote", headers=headers, json={"key": "p"}).status_code == 200
    assert (
        client.get("/autonomy/skills/case-investigation/active", headers=headers).json()["version"]
        == "2"
    )
    assert (
        client.post(
            "/autonomy/skills/case-investigation/rollback", headers=headers, json={"key": "r"}
        ).status_code
        == 200
    )


def test_review_ack_requires_exact_current_proof(monkeypatch):
    rails, case, client = setup(monkeypatch)
    from agents.supervisor import Supervisor

    headers = {"Authorization": "Bearer private-test-token"}
    approvals = HumanApprovals(rails)
    bad = approvals.request(case, "case_review", {"reason": "late"}, NOW, "bad")
    assert (
        client.post(
            f"/autonomy/approvals/{bad['id']}/decide",
            headers=headers,
            json={"approve": True, "digest": bad["digest"]},
        ).status_code
        == 422
    )
    proof = Supervisor(rails)._proof(case, NOW)
    ticket = approvals.request(
        case, "case_review", {"proof_fingerprint": proof["proof_fingerprint"]}, NOW, "good"
    )
    assert (
        client.post(
            f"/autonomy/approvals/{ticket['id']}/decide",
            headers=headers,
            json={"approve": True, "digest": ticket["digest"]},
        ).status_code
        == 200
    )
    acknowledgement = next(e for e in rails.events() if e.type == "human_anomaly_acknowledged")
    assert acknowledgement.payload["actor"] == "controller.dana"
    assert acknowledgement.payload["proof_fingerprint"] == proof["proof_fingerprint"]


def test_case_trace_and_durable_wake_are_scoped_and_idempotent(monkeypatch):
    _rails, case, client = setup(monkeypatch)
    headers = {"Authorization": "Bearer private-test-token"}
    response = client.get(f"/autonomy/cases/{case.id}/events", headers=headers)
    assert response.status_code == 200
    assert all(e["payload"]["case_id"] == case.id for e in response.json())
    for _ in range(2):
        assert (
            client.post(
                f"/autonomy/cases/{case.id}/wake", headers=headers, json={"key": "manual"}
            ).status_code
            == 200
        )
    assert len(client.get("/autonomy/jobs", headers=headers).json()) == 1
    assert (
        client.post(
            "/autonomy/cases/missing/wake", headers=headers, json={"key": "bad"}
        ).status_code
        == 404
    )
