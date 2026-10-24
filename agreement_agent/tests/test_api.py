"""The agreement agent's upload API: input a PDF or .txt, output a Review."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agreement_agent import api
from agreement_agent.api import app

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
client = TestClient(app)
SAVED = []


@pytest.fixture(autouse=True)
def _no_spacetime(monkeypatch):
    SAVED.clear()
    monkeypatch.setattr(api, "save", lambda r, rules: SAVED.append(api.agreement_row(r)))


def _upload(name, data, mime):
    return client.post("/review?model=false", files={"file": (name, data, mime)})


def test_complete_agreement_has_every_field_and_no_escalation():
    pdf = (SAMPLES / "rio_grande_services_agreement.pdf").read_bytes()
    r = _upload("rio.pdf", pdf, "application/pdf")
    assert r.status_code == 200
    body = r.json()["review"]
    assert r.json()["saved_to_spacetime"]
    assert body["kind"] == "agreement" and body["complete"] and body["escalation"] is None
    due = next(f for f in body["fields"] if f["name"] == "payment_due")
    assert due["status"] == "found" and due["quote"] and due["location"] == "§3"


def test_incomplete_agreement_drafts_an_escalation_to_the_owner():
    pdf = (SAMPLES / "greenedge_landscaping_agreement.pdf").read_bytes()
    body = _upload("greenedge.pdf", pdf, "application/pdf").json()["review"]
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


def test_saved_row_holds_the_terms_and_what_is_missing():
    pdf = (SAMPLES / "greenedge_landscaping_agreement.pdf").read_bytes()
    _upload("greenedge.pdf", pdf, "application/pdf")
    [row] = SAVED
    assert row[2] == "agreement" and row[4] == {"some": "GreenEdge Landscaping LLC"}
    assert row[5] == {"none": []} and row[9] is False
    assert {"payment_due", "payment_info"} <= set(row[10])
    assert row[11]["some"].startswith("Action needed")
