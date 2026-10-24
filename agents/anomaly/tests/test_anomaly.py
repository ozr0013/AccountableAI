"""Anomaly detector: a manager statement's amounts against what Oakline approved to pay."""

from datetime import UTC, date, datetime

from agents.anomaly import AnomalyAgent
from agents.testing import ScriptedLLM
from contracts.models import AlertKind, Document, Event, LedgerLine, SourceKind
from workflow.book import WorkflowBook
from workflow.state import Route
from workflow.steps import Steps

LEDGER = [LedgerLine(invoice_id="INV-KW-0301", manager_id="keyway", vendor_id="v-riogrande",
                     amount=1000.0, funded_amount=1000.0),
          LedgerLine(invoice_id="INV-KW-0302", manager_id="keyway", vendor_id="v-sparkline",
                     amount=450.0, funded_amount=450.0)]


def _stmt(lines=None, text="", sid="doc-stmt-test"):
    return Document(id=sid, kind=SourceKind.manager_statement, manager_id="keyway",
                    dated=date(2026, 4, 30), text=text or "Keyway statement.",
                    data={"lines": lines} if lines else {})


def test_matching_statement_raises_nothing():
    r = AnomalyAgent(None).detect_anomalies(_stmt([
        {"line": "L1", "invoice_id": "INV-KW-0301", "amount": 1000.0, "status": "paid"},
        {"line": "L2", "invoice_id": "INV-KW-0302", "amount": 450.0, "status": "paid"}]), LEDGER)
    assert r.anomalies == [] and r.matched == ["INV-KW-0301", "INV-KW-0302"] and r.checked == 2


def test_paid_500_when_we_approved_1000_is_caught():
    r = AnomalyAgent(None).detect_anomalies(_stmt([
        {"line": "L1", "invoice_id": "INV-KW-0301", "amount": 500.0, "status": "paid"}]), LEDGER)
    [a] = r.anomalies
    assert a.reason == "amount_differs" and a.expected_amount == 1000.0 and a.difference == -500.0
    assert a.citation.source_id == "doc-stmt-test" and a.citation.locator == "L1"


def test_invoice_we_never_approved_is_caught():
    r = AnomalyAgent(None).detect_anomalies(_stmt([
        {"line": "L4", "invoice_id": "INV-KW-0999", "amount": 300.0, "status": "paid"}]), LEDGER)
    [a] = r.anomalies
    assert a.reason == "not_on_ledger" and a.expected_amount == 0.0


def test_text_only_statement_is_read_by_rules_without_a_model():
    s = _stmt(text="Keyway statement, April. L1: INV-KW-0301 Rio Grande $1,000.00 PAID; "
                   "L2: INV-KW-0302 Sparkline $400.00 PAID.")
    r = AnomalyAgent(None).detect_anomalies(s, LEDGER)
    assert r.read_by == "rules" and [a.invoice_id for a in r.anomalies] == ["INV-KW-0302"]


def test_model_reading_must_quote_the_statement_or_rules_win():
    s = _stmt(text="Keyway statement. L1: INV-KW-0301 $500.00 paid to Rio Grande.")
    made_up = {"lines": [{"line": "L1", "invoice_id": "INV-KW-0301", "amount_text": "$1,000.00"}]}
    llm = ScriptedLLM(made_up, made_up)                  # rejected twice -> regex reader
    r = AnomalyAgent(llm).detect_anomalies(s, LEDGER)
    assert r.read_by == "rules" and r.anomalies[0].statement_amount == 500.0
    ok = {"lines": [{"line": "L1", "invoice_id": "INV-KW-0301", "amount_text": "$500.00"}]}
    r = AnomalyAgent(ScriptedLLM(ok)).detect_anomalies(s, LEDGER)
    assert r.read_by == "model" and r.anomalies[0].difference == -500.0


def test_only_a_manager_statement_triggers_it():
    def ev(kind):
        return Event(id=f"ev-{kind}", type="document_ingested", valid_from=datetime.now(UTC),
                     learned_at=datetime.now(UTC), payload={"kind": kind, "data": {}})
    assert Steps._route_for(ev("manager_statement")) == Route.statement
    for other in ("manager_bank", "vendor_invoice", "vendor_notice", "funding"):
        assert Steps._route_for(ev(other)) != Route.statement


def test_workflow_alerts_owner_drafts_note_to_middleman_and_withdraws_when_corrected():
    from workflow.tests.conftest import make_workflow
    from world import generate
    world = generate(scenarios=(3,), n_clean=5)
    wf = make_workflow()
    wf.replay(world.events())
    rails = wf.deps.rails
    line = next(l for l in rails.ledger("keyway") if l.invoice_id == "INV-KW-0301")

    def ingest(amount, sid, day):
        when = datetime(2026, 5, day, tzinfo=UTC)
        doc = _stmt([{"line": "L2", "invoice_id": line.invoice_id, "amount": amount, "status": "paid",
                      "on": "2026-05-01"}], sid=sid)
        return wf.ingest(Event(id=f"ev-{sid}", type="document_ingested", manager_id="keyway",
                               valid_from=when, learned_at=when,
                               payload=doc.model_dump(mode="json")))

    r = ingest(line.amount / 2, "doc-stmt-half", 7)
    assert r.route == "statement"
    [al] = [a for a in WorkflowBook.load(rails).open_alerts("keyway")
            if a.draft.kind == AlertKind.statement_mismatch]
    assert al.draft.amount == line.amount / 2 and al.draft.invoice_ids == [line.invoice_id]
    assert "controller approves" in al.draft.recommended_action          # note to the middleman
    assert al.draft.claims[0].citations[0].source_id == "doc-stmt-half"

    ingest(line.amount, "doc-stmt-fixed", 8)
    assert not [a for a in WorkflowBook.load(rails).open_alerts("keyway")
                if a.draft.kind == AlertKind.statement_mismatch]
