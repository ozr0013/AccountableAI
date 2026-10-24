"""Agreement agent, offline: parser, field finders, the LangGraph flow, the quote guard, the
connectors, and the model tool loop driven by a scripted (free) model."""

from __future__ import annotations

import base64
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from agreement_agent.connectors import Gmail, LocalFolder, from_eml
from agreement_agent.graph import Tools, review
from agreement_agent.models import SourceDoc
from agreement_agent.parser import parse

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def _doc(name: str) -> SourceDoc:
    return next(d for d in LocalFolder(SAMPLES).fetch() if d.filename == name)


def _txt(text: str, name: str = "agreement.txt") -> SourceDoc:
    return SourceDoc(id="doc-t", source="local", filename=name, mime="text/plain", data=text.encode())


def _fields(r):
    return {f.name: f for f in r.fields}


def test_parser_reads_pdf_header_and_numbered_sections():
    p = parse(_doc("rio_grande_services_agreement.pdf"))
    assert p.kind == "agreement"
    assert p.header[0] == "SERVICES AGREEMENT"
    assert [s.number for s in p.sections if s.number] == ["1", "2", "3", "4", "5", "6"]
    assert p.where("within thirty (30) days of receipt") == "§3"


def test_complete_agreement_needs_no_owner():
    r = review(_doc("rio_grande_services_agreement.pdf"))
    f = _fields(r)
    assert r.complete and r.escalation is None
    assert f["owner_name"].value == "Oakline Homes LLC"
    assert f["vendor_name"].value == "Rio Grande Plumbing LLC"
    assert "30" in f["payment_due"].value and f["payment_due"].location == "§3"
    assert "routing number 102000021" in f["payment_info"].value
    for x in r.fields:
        if x.status == "found":                        # every value is backed by a real quote
            assert x.quote and x.quote.lower().replace("\n", " ") in parse(_doc(r.filename)).text.lower().replace("\n", " ")


def test_missing_due_date_and_payment_info_escalate_to_the_owner():
    r = review(_doc("greenedge_landscaping_agreement.pdf"))
    f = _fields(r)
    assert not r.complete
    assert f["payment_due"].status == "missing" and f["payment_info"].status == "missing"
    assert f["vendor_name"].value == "GreenEdge Landscaping LLC"
    e = r.escalation
    assert e and e.status == "drafted" and e.severity == "high"
    assert {i.name for i in e.items} == {"payment_due", "payment_info"}
    assert "GreenEdge Landscaping LLC" in e.body and "Not sent" in e.body


def test_invoice_by_email_is_checked_as_an_invoice():
    docs = list(LocalFolder(SAMPLES / "inbox").fetch())
    assert len(docs) == 1 and docs[0].sender == "billing@sparkline-electric.example"
    r = review(docs[0])
    f = _fields(r)
    assert r.kind == "invoice"
    assert f["invoice_number"].value == "SE-0402" and f["payment_amount"].value == "$900.00"
    assert {i.name for i in r.escalation.items} == {"payment_due", "payment_info"}


def test_quote_guard_refuses_text_that_is_not_in_the_document():
    t = Tools(parse(_doc("greenedge_landscaping_agreement.pdf")), by="model")
    assert t.record_field("payment_due", "Net 30", "Payment is due Net 30.").startswith("error")
    assert "payment_due" not in t.results
    assert t.record_field("payment_amount", "$1,250", "Owner will pay Contractor a monthly fee of $1,250 for the services.") == "recorded"


def test_documents_that_are_not_agreements_or_invoices_are_skipped_not_escalated():
    r = review(_txt("RESUME\nJane Doe\nSoftware engineer. Skills: Python, SQL.\nEducation: State University, 2024.", "resume.pdf.txt"))
    assert r.kind == "other" and not r.relevant and r.escalation is None and r.fields == []


def test_unreadable_file_is_unclear_not_guessed():
    r = review(SourceDoc(id="doc-scan", source="local", filename="scan.pdf", mime="application/pdf", data=b"%PDF-1.4 junk"))
    assert not r.complete and all(f.status == "unclear" for f in r.fields)


def test_text_agreement_with_net_terms_and_check_payment():
    r = review(_txt(
        "MAINTENANCE AGREEMENT\nThis agreement is between Oakline Homes LLC (\"Client\") and Peak Roofing LLC "
        "(\"Contractor\").\n1. Price. The contract price is $4,800.\n2. Terms. Payment terms are Net 15 from "
        "the invoice date.\n3. Payment. Checks shall be made payable to Peak Roofing LLC.\nBy: Dana Ortiz\nBy: Sam Peak"))
    f = _fields(r)
    assert r.complete, [(x.name, x.status) for x in r.fields]
    assert f["payment_due"].value.lower().startswith("net 15")
    assert f["vendor_name"].value == "Peak Roofing LLC"


def test_eml_attachment_and_gmail_connector_with_a_stand_in_service():
    pdf = (SAMPLES / "sparkline_invoice_SE-0402.pdf").read_bytes()
    assert from_eml((SAMPLES / "inbox" / "invoice_SE-0402.eml").read_bytes())[0].data == pdf

    class Svc:                                   # the bits of the Gmail API the connector uses
        def users(self): return self
        def messages(self): return self
        def attachments(self): return self
        def list(self, **kw): return self._r({"messages": [{"id": "m1"}]})
        def get(self, **kw):
            if "messageId" in kw:
                return self._r({"data": base64.urlsafe_b64encode(pdf).decode()})
            return self._r({"internalDate": "1776000000000", "payload": {
                "headers": [{"name": "From", "value": "billing@sparkline-electric.example"},
                            {"name": "Subject", "value": "Invoice SE-0402"}],
                "parts": [{"mimeType": "text/plain", "filename": "", "body": {"data": ""}},
                          {"mimeType": "application/pdf", "filename": "SE-0402.pdf", "body": {"attachmentId": "a1"}}]}})
        def _r(self, v):
            class X:
                def execute(self_inner): return v
            return X()

    docs = list(Gmail(service=Svc()).fetch())
    assert len(docs) == 1 and docs[0].data == pdf and docs[0].message_id == "m1"
    assert review(docs[0]).kind == "invoice"


def test_gmail_sent_on_reads_only_that_days_mail():
    today = datetime.combine(date(2026, 10, 24), time(9, 30)).astimezone()
    stamps = {"m-today": today, "m-old": today - timedelta(days=200)}   # Gmail returns both
    asked: list[str] = []

    class Svc:
        def users(self): return self
        def messages(self): return self
        def list(self, **kw):
            asked.append(kw["q"])
            return self._r({"messages": [{"id": k} for k in stamps]})
        def get(self, **kw):
            ms = str(int(stamps[kw["id"]].timestamp() * 1000))
            return self._r({"internalDate": ms, "payload": {"headers": [], "parts": [
                {"mimeType": "text/plain", "filename": f"{kw['id']}.txt", "body": {"data": base64.urlsafe_b64encode(b"x").decode()}}]}})
        def _r(self, v):
            class X:
                def execute(self_inner): return v
            return X()

    docs = list(Gmail(service=Svc()).fetch("has:attachment", sent_on=date(2026, 10, 24)))
    assert [d.filename for d in docs] == ["m-today.txt"]          # the old one is dropped even if Gmail returns it
    assert "after:" in asked[0] and "before:" in asked[0]


def test_batch_samples_match_their_manifest():
    import json
    batch = SAMPLES / "batch"
    if not (batch / "manifest.json").exists():
        pytest.skip("run samples/make_batch.py first")
    from agreement_agent.fields import SPECS
    for m in json.loads((batch / "manifest.json").read_text()):
        r = review(next(d for d in LocalFolder(batch).fetch() if d.filename == m["file"]))
        if m["expect"] == "skipped":
            assert not r.relevant, m["file"]
            continue
        req = {s.name for s in SPECS[r.kind] if s.required}
        assert sorted(f.name for f in r.fields if f.status != "found" and f.name in req) == sorted(m["expect"]), m["file"]


def test_model_tool_loop_with_a_scripted_model():
    """The model path: Claude's tool calls go through the same guarded tools. Scripted, free."""
    from langchain_core.messages import AIMessage
    q = "Owner will pay Contractor a monthly fee of $1,250 for the services."
    script = [
        [("read_header", {}), ("list_sections", {})],
        [("read_section", {"number": "4"})],
        [("record_field", {"field": "payment_amount", "value": "$1,250", "quote": q}),
         ("record_field", {"field": "payment_due", "value": "Net 30", "quote": "Payment is due Net 30."}),
         ("record_field", {"field": "owner_name", "value": "Oakline Homes LLC",
                           "quote": 'This Agreement is entered into by Oakline Homes LLC ("Owner") and GreenEdge Landscaping LLC ("Contractor").'}),
         ("record_field", {"field": "vendor_name", "value": "GreenEdge Landscaping LLC",
                           "quote": 'This Agreement is entered into by Oakline Homes LLC ("Owner") and GreenEdge Landscaping LLC ("Contractor").'})],
        [("mark_unclear", {"field": "payment_info", "reason": "§4 says details will be provided separately"}),
         ("mark_missing", {"field": "payment_due", "reason": "no due date or terms in any section"})],
        [],
    ]

    class Scripted:
        def __init__(self): self.turn = 0
        def invoke(self, msgs):
            calls = script[min(self.turn, len(script) - 1)]
            self.turn += 1
            return AIMessage(content="" if calls else "Landscaping agreement without payment terms.",
                             tool_calls=[{"name": n, "args": a, "id": f"c{self.turn}{i}"} for i, (n, a) in enumerate(calls)],
                             usage_metadata={"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100})

    r = review(_doc("greenedge_landscaping_agreement.pdf"), use_model=True, model=Scripted())
    f = _fields(r)
    assert r.mode == "model" and r.cost_usd > 0
    assert f["payment_amount"].status == "found" and f["payment_amount"].by == "model"
    assert f["payment_due"].status == "missing"            # the invented "Net 30" was refused
    assert f["payment_info"].status == "unclear"
    assert any("refused payment_due" in s for s in r.steps)
    assert r.escalation and {i.name for i in r.escalation.items} == {"payment_due", "payment_info"}


@pytest.mark.skipif(True, reason="live Claude run: costs money, run by hand with --model")
def test_live_model():  # pragma: no cover
    pass
