"""The agreement agent's upload API: input a PDF or .txt, output a Review."""
from pathlib import Path

from fastapi.testclient import TestClient

from agreement_agent.api import app

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
client = TestClient(app)


def _upload(name, data, mime):
    return client.post("/review?model=false", files={"file": (name, data, mime)})


def test_complete_agreement_has_every_field_and_no_escalation():
    pdf = (SAMPLES / "rio_grande_services_agreement.pdf").read_bytes()
    r = _upload("rio.pdf", pdf, "application/pdf")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "agreement" and body["complete"] and body["escalation"] is None
    due = next(f for f in body["fields"] if f["name"] == "payment_due")
    assert due["status"] == "found" and due["quote"] and due["location"] == "§3"


def test_incomplete_agreement_drafts_an_escalation_to_the_owner():
    pdf = (SAMPLES / "greenedge_landscaping_agreement.pdf").read_bytes()
    body = _upload("greenedge.pdf", pdf, "application/pdf").json()
    assert not body["complete"]
    assert body["escalation"]["to"] == "owner" and body["escalation"]["status"] == "drafted"
    missing = {f["name"] for f in body["fields"] if f["status"] == "missing"}
    assert {"payment_due", "payment_info"} <= missing


def test_rejects_unsupported_and_empty_uploads():
    assert _upload("x.png", b"\x89PNG", "image/png").status_code == 415
    assert _upload("x.txt", b"", "text/plain").status_code == 413


def test_claude_is_the_default_and_needs_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post("/review", files={"file": ("a.txt", b"Agreement", "text/plain")})
    assert r.status_code == 400
