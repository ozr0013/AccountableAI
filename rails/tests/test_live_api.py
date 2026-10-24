"""The live API: facts in, the system reacts, one human touch. Rules mode, throwaway schema."""

import time

import pytest
from fastapi.testclient import TestClient

from agents import RealAgents
from contracts.fakes import FakeVerifier
from rails import RealRails
from rails.console import api
from rails.live.system import LiveSystem
from world import generate
from world.generator import ALL_SCENARIOS


@pytest.fixture
def client(schema, monkeypatch):
    rails = RealRails(schema=schema)
    rails.load(generate(13, ALL_SCENARIOS, 30).events())
    system = LiveSystem(rails, agents=RealAgents(None), verifier=FakeVerifier())
    system.warm()
    monkeypatch.setattr(api, "_live", system)
    monkeypatch.delenv("DEMO_TOKEN", raising=False)
    return TestClient(api.app)


def wait_for(client, job_id, headers=None, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/jobs/{job_id}", headers=headers).json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_a_fact_is_recorded_at_once_and_the_workflow_runs_in_the_background(client):
    r = client.post("/presets/invoice_received_correction", json={"params": {}})
    body = r.json()
    assert r.status_code == 200 and body["affected"] == ["INV-KW-0402"]
    assert body["event_ids"] and body["job_id"] and body["workflow"] is None
    job = wait_for(client, body["job_id"])
    assert job["status"] == "done" and len(job["result"][0]["withdrawn"]) == 1
    feed = client.get("/feed").json()
    assert any("Alert withdrawn" in i["text"] for i in feed)
    assert any(i["actor"] == "input" and "invoice:INV-KW-0402.received is 2026-04-15" in i["text"]
               for i in feed)


def test_wait_true_runs_the_workflow_before_answering(client):
    r = client.post("/text", json={"text": "Keyway says they paid the electrician",
                                   "manager_id": "keyway"}, params={"wait": True}).json()
    assert r["job_id"] is None and r["workflow"][0]["route"] == "reply"


def test_clock_advance_defaults_to_the_next_deadline(client):
    r = client.post("/clock/advance", params={"wait": True}).json()
    assert r["now"] == "2026-04-25" and r["reason"] == "INV-KW-0701 becomes overdue"
    assert client.get("/stats").json()["today"] == "2026-04-25"
    back = client.post("/clock/advance", json={"to": "2026-04-20"})
    assert back.status_code == 422 and "forward" in back.json()["detail"]["error"]


def test_the_human_touch_over_http(client):
    client.post("/funding-review", json={"manager_id": "keyway", "amount": 18400}, params={"wait": True})
    pending = {a["proposed_hold"]: a for a in client.get("/approvals").json()}
    aid = pending[3140.0]["approval_id"]
    assert pending[3140.0]["evidence"] and pending[3140.0]["funding_amount"] == 18400.0
    assert client.post(f"/approvals/{aid}/decide", json={"approver": "case_agent", "approve": True}
                       ).status_code == 403
    assert client.post(f"/approvals/{aid}/decide", json={"approver": "dana", "approve": True}
                       ).status_code == 403                      # not a controller
    ok = client.post(f"/approvals/{aid}/decide", json={"approver": "controller.dana", "approve": True})
    assert ok.status_code == 200 and ok.json()["proposed_hold"] == 3140.0
    assert client.post(f"/approvals/{aid}/decide", json={"approver": "controller.dana", "approve": True}
                       ).status_code == 409                      # already decided
    assert client.post("/approvals/nope/decide", json={"approver": "controller.dana", "approve": True}
                       ).status_code == 404
    s = client.get("/stats").json()
    assert s["human_decisions"] == 1 and s["automatic_actions"] > 0
    assert s["human_decisions_per_100_invoices"] > 0
    assert s["mode"]["reminders"].startswith("issued and recorded")


def test_token_protects_everything_but_health(client, monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN", "s3cret")
    assert client.get("/health").status_code == 200
    for path in ("/stats", "/feed", "/approvals", "/presets"):
        assert client.get(path).status_code == 401
    assert client.get("/stats", headers={"X-Demo-Token": "wrong"}).status_code == 401
    assert client.get("/stats", headers={"X-Demo-Token": "s3cret"}).status_code == 200
    assert client.post("/clock/advance").status_code == 401


def test_cors_allows_the_dashboard_origin(client):
    r = client.options("/stats", headers={"Origin": "http://localhost:3000",
                                          "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_a_failing_job_is_reported_not_lost(client):
    job_id = api._jobs.submit("boom", lambda: 1 / 0)
    j = wait_for(client, job_id)
    assert j["status"] == "failed" and "ZeroDivisionError" in j["error"]
    assert client.get("/jobs/nope").status_code == 404
