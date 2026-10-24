"""A controller must confirm an agreement with missing terms before its rules are relied on."""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agreement_agent import api

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
TOKEN = "test-controller-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
COLUMNS = ["doc_id", "filename", "kind", "owner_name", "vendor_name", "payment_due",
           "payment_amount", "payment_info", "effective_date", "complete", "missing",
           "escalation_subject", "escalation_body", "fields", "steps", "mode", "cost_usd",
           "status", "confirmed_by", "controller_note"]


def _plain(v):
    return v.get("some") if isinstance(v, dict) and set(v) <= {"some", "none"} else v


class FakeSpacetime:
    """The two reducers and the SELECTs the API uses, in memory."""

    def __init__(self):
        self.agreements, self.rules = {}, {}

    def call(self, reducer, args):
        if reducer == "save_agreement":
            self.agreements[args[0]] = {c: _plain(v) for c, v in zip(COLUMNS, args, strict=True)}
        elif reducer == "set_agreement_rules":
            self.rules[args[0]] = [{**{k: _plain(v) for k, v in r.items()}, "doc_id": args[0]}
                                   for r in args[2]]

    def query(self, sql):
        doc = re.search(r"doc_id = '([^']+)'", sql)
        if "agreement_rule" in sql:
            return list(self.rules.get(doc.group(1), []))
        rows = self.agreements.values()
        return [r for r in rows if not doc or r["doc_id"] == doc.group(1)]


@pytest.fixture
def stdb(monkeypatch):
    fake = FakeSpacetime()
    monkeypatch.setattr(api, "_mirror", lambda: fake)
    monkeypatch.setenv("CONTROLLER_API_TOKEN", TOKEN)
    monkeypatch.setenv("CONTROLLER_ID", "controller.dana")
    return fake


client = TestClient(api.app)


def _upload(name):
    pdf = (SAMPLES / name).read_bytes()
    return client.post("/review?model=false", files={"file": (name, pdf, "application/pdf")}).json()


def test_complete_agreement_is_active_without_a_controller(stdb):
    out = _upload("rio_grande_services_agreement.pdf")
    assert out["status"] == "active" and all(r["status"] == "active" for r in out["rules"])


def test_incomplete_agreement_waits_for_the_controller(stdb):
    out = _upload("greenedge_landscaping_agreement.pdf")
    assert out["status"] == "needs_controller"
    kinds = {r["rule_type"]: r["status"] for r in out["rules"]}
    assert kinds["amount"] == "pending_controller" and kinds["hold_payment"] == "blocking"


def test_controller_supplies_missing_terms_and_rules_go_active(stdb):
    doc = _upload("greenedge_landscaping_agreement.pdf")["review"]["doc_id"]
    body = {"values": {"payment_due": "within 15 days of invoice receipt",
                       "payment_info": "ACH to Desert Bank, routing 123456789, account 99887766"},
            "note": "Confirmed by phone with GreenEdge"}
    r = client.post(f"/agreements/{doc}/confirm", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "confirmed" and out["confirmed_by"] == "controller.dana"
    rules = {x["rule_type"]: x for x in out["rules"]}
    assert "hold_payment" not in rules and all(x["status"] == "active" for x in out["rules"])
    assert json.loads(json.dumps(rules["pay_within_days"]["params"]))["days"] == 15
    assert rules["pay_to_account"]["location"] == "controller:controller.dana"


def test_confirm_needs_every_missing_term_and_only_those(stdb):
    doc = _upload("greenedge_landscaping_agreement.pdf")["review"]["doc_id"]
    partial = {"values": {"payment_due": "Net 30"}}
    assert client.post(f"/agreements/{doc}/confirm", json=partial, headers=AUTH).status_code == 409
    sneaky = {"values": {"payment_due": "Net 30", "payment_info": "check", "payment_amount": "$1"}}
    assert client.post(f"/agreements/{doc}/confirm", json=sneaky, headers=AUTH).status_code == 409


def test_only_the_controller_can_decide(stdb):
    doc = _upload("greenedge_landscaping_agreement.pdf")["review"]["doc_id"]
    body = {"values": {"payment_due": "Net 30", "payment_info": "check payable to GreenEdge"}}
    assert client.post(f"/agreements/{doc}/confirm", json=body).status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert client.post(f"/agreements/{doc}/confirm", json=body, headers=bad).status_code == 401


def test_complete_agreement_cannot_be_confirmed_again(stdb):
    doc = _upload("rio_grande_services_agreement.pdf")["review"]["doc_id"]
    r = client.post(f"/agreements/{doc}/confirm", json={"values": {}}, headers=AUTH)
    assert r.status_code == 409


def test_controller_can_reject(stdb):
    doc = _upload("greenedge_landscaping_agreement.pdf")["review"]["doc_id"]
    r = client.post(f"/agreements/{doc}/reject", json={"note": "Unsigned draft"}, headers=AUTH)
    out = r.json()
    assert out["status"] == "rejected" and out["controller_note"] == "Unsigned draft"
    assert all(x["status"] == "rejected" for x in out["rules"])


def test_sign_off_is_off_without_a_token(stdb, monkeypatch):
    monkeypatch.delenv("CONTROLLER_API_TOKEN")
    doc = _upload("greenedge_landscaping_agreement.pdf")["review"]["doc_id"]
    assert client.post(f"/agreements/{doc}/reject", json={"note": "x"}).status_code == 503
