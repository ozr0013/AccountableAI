from datetime import UTC, date, datetime

import pytest

from agents import RealAgents
from contracts.models import Document
from rails.events.ingest import document_event
from workflow.book import WorkflowBook
from workflow.memrails import MemoryRails

from .conftest import make_workflow


def setup_payment(amount=1240, vendor="v-riogrande", on="2026-04-22"):
    rails = MemoryRails.from_world((3,), n_clean=0)
    wf = make_workflow(rails, agents=RealAgents(None))
    bank = Document(id="bank-new", kind="manager_bank", manager_id="keyway",
                    dated=date(2026, 4, 22), data={"payments": [{
                        "invoice_id": "INV-KW-0301", "vendor_id": vendor,
                        "amount": amount, "on": on}]})
    event = document_event(bank, datetime(2026, 4, 22, 12, tzinfo=UTC))
    wf.ingest(event)
    return wf, event


def reviews(wf):
    return [e for e in wf.deps.rails.events() if e.type == "payment_reviewed"
            and e.invoice_id == "INV-KW-0301"]


@pytest.mark.parametrize("amount,vendor,expected", [
    (1300, "v-riogrande", "overpayment"),
    (1000, "v-riogrande", "underpayment"),
    (1240, "v-other", "wrong_vendor"),
])
def test_payment_triggers_cited_review_and_escalation(amount, vendor, expected):
    wf, _ = setup_payment(amount, vendor)
    [review] = reviews(wf)
    assert expected in {f["code"] for f in review.payload["findings"]}
    assert review.payload["evidence"]
    updates = [e for e in wf.deps.rails.events() if e.type == "finance_update_queued"]
    assert any(e.payload["review_id"] == review.id
               and e.payload["recipients"] == ["management", "owner"] for e in updates)


def test_bank_payment_requests_statement_once_and_restart_is_safe():
    wf, event = setup_payment()
    [review] = reviews(wf)
    assert "late_payment" in {f["code"] for f in review.payload["findings"]}
    assert "statement_missing" in {f["code"] for f in review.payload["findings"]}
    wf.handle(event)
    restarted = make_workflow(wf.deps.rails, agents=RealAgents(None))
    from workflow.monitor import Monitor
    Monitor(restarted).tick(datetime(2026, 4, 23, 12, tzinfo=UTC))
    assert len(reviews(wf)) == 1
    requests = [e for e in wf.deps.rails.events() if e.type == "statement_request_drafted"]
    assert len(requests) == 1
    assert requests[0].payload["requires_approval"] is True


def test_statement_arrival_rechecks_payment_and_resolves_missing_statement():
    wf, _ = setup_payment()
    statement = Document(id="statement-new", kind="manager_statement", manager_id="keyway",
                         dated=date(2026, 4, 23), data={"lines": [{
                             "invoice_id": "INV-KW-0301", "amount": 1240,
                             "status": "paid", "on": "2026-04-22"}]})
    wf.ingest(document_event(statement, datetime(2026, 4, 23, 12, tzinfo=UTC)))
    assert len(reviews(wf)) == 2
    latest = reviews(wf)[-1]
    assert "statement_missing" not in {f["code"] for f in latest.payload["findings"]}
    assert "statement_amount_mismatch" not in {f["code"] for f in latest.payload["findings"]}


def test_wrong_vendor_is_not_treated_as_payment_proof():
    wf, _ = setup_payment(vendor="v-other")
    assert any(a.draft.amount == 1240 for a in WorkflowBook.load(wf.deps.rails).open_alerts())


def test_interrupted_review_repairs_missing_notifications_on_retry(monkeypatch):
    rails = MemoryRails.from_world((3,), n_clean=0)
    wf = make_workflow(rails, agents=RealAgents(None))
    original = rails.append_event

    def fail_notification(event):
        if event.type == "finance_update_queued":
            raise RuntimeError("connection lost")
        original(event)

    monkeypatch.setattr(rails, "append_event", fail_notification)
    bank = Document(id="bank-crash", kind="manager_bank", manager_id="keyway",
                    dated=date(2026, 4, 22), data={"payments": [{
                        "invoice_id": "INV-KW-0301", "vendor_id": "v-riogrande",
                        "amount": 1240, "on": "2026-04-22"}]})
    event = document_event(bank, datetime(2026, 4, 22, 12, tzinfo=UTC))
    with pytest.raises(RuntimeError, match="connection lost"):
        wf.ingest(event)
    monkeypatch.setattr(rails, "append_event", original)
    make_workflow(rails, agents=RealAgents(None)).handle(event)
    assert len(reviews(wf)) == 1
    assert any(e.type == "finance_update_queued" for e in rails.events())
    assert any(e.type == "statement_request_drafted" for e in rails.events())


def test_on_time_correct_payment_with_statement_is_verified():
    wf, _ = setup_payment(on="2026-03-10")
    [review] = reviews(wf)
    assert review.payload["status"] == "verified"


def test_statement_wrong_amount_is_escalated():
    wf, _ = setup_payment()
    statement = Document(id="statement-wrong", kind="manager_statement", manager_id="keyway",
                         dated=date(2026, 4, 23), data={"lines": [{
                             "invoice_id": "INV-KW-0301", "amount": 999,
                             "status": "paid", "on": "2026-04-22"}]})
    wf.ingest(document_event(statement, datetime(2026, 4, 23, 12, tzinfo=UTC)))
    assert "statement_amount_mismatch" in {f["code"] for f in reviews(wf)[-1].payload["findings"]}


def test_unique_bank_match_without_invoice_reference_is_reviewed():
    rails = MemoryRails.from_world((3,), n_clean=0)
    wf = make_workflow(rails, agents=RealAgents(None))
    bank = Document(id="bank-no-reference", kind="manager_bank", manager_id="keyway",
                    dated=date(2026, 3, 10), data={"payments": [{
                        "vendor_id": "v-riogrande", "amount": 1240, "on": "2026-03-10"}]})
    wf.ingest(document_event(bank, datetime(2026, 4, 22, 12, tzinfo=UTC)))
    assert reviews(wf)[-1].payload["paid_amount"] == 1240


def test_superseded_bank_evidence_reopens_review():
    wf, event = setup_payment()
    replacement = Document(id="bank-retracted", kind="manager_bank", manager_id="keyway",
                           dated=date(2026, 4, 23), data={"payments": []})
    corrected = document_event(replacement, datetime(2026, 4, 23, 12, tzinfo=UTC))
    corrected.supersedes = event.id
    wf.ingest(corrected)
    from workflow.monitor import Monitor
    Monitor(wf).tick(datetime(2026, 4, 23, 12, tzinfo=UTC))
    assert reviews(wf)[-1].payload["paid_amount"] == 0
    assert "payment_evidence_removed" in {f["code"] for f in reviews(wf)[-1].payload["findings"]}


def test_agreement_authorized_holdback_is_not_underpayment():
    from workflow.run import scan_events
    rails = MemoryRails.from_world((1,), n_clean=0)
    wf = make_workflow(rails, agents=RealAgents(None))
    wf.replay(scan_events(["keyway"], date(2026, 4, 22)))
    checks = [e for e in rails.events() if e.type == "payment_reviewed"]
    assert checks
    assert all("underpayment" not in {f["code"] for f in e.payload["findings"]} for e in checks)


def test_retry_with_intervening_statement_preserves_original_review(monkeypatch):
    rails = MemoryRails.from_world((3,), n_clean=0)
    wf = make_workflow(rails, agents=RealAgents(None))
    original = rails.append_event
    interrupt = True

    def strict_append(event):
        old = rails.store.get(event.id)
        if old and old != event:
            raise ValueError("conflicting event content")
        if event.type == "finance_update_queued" and interrupt:
            raise RuntimeError("connection lost")
        original(event)

    monkeypatch.setattr(rails, "append_event", strict_append)
    bank = Document(id="bank-interrupted", kind="manager_bank", manager_id="keyway",
                    dated=date(2026, 4, 22), data={"payments": [{
                        "invoice_id": "INV-KW-0301", "vendor_id": "v-riogrande",
                        "amount": 1240, "on": "2026-04-22"}]})
    event = document_event(bank, datetime(2026, 4, 22, 12, tzinfo=UTC))
    with pytest.raises(RuntimeError, match="connection lost"):
        wf.ingest(event)
    statement = Document(id="statement-during-downtime", kind="manager_statement",
                         manager_id="keyway", dated=date(2026, 4, 22), data={"lines": [{
                             "invoice_id": "INV-KW-0301", "amount": 1240,
                             "status": "paid", "on": "2026-04-22"}]})
    statement_event = document_event(statement, datetime(2026, 4, 22, 13, tzinfo=UTC))
    rails.append_event(statement_event)
    interrupt = False
    restarted = make_workflow(rails, agents=RealAgents(None))
    restarted.handle(event)
    assert len(reviews(wf)) == 1
    restarted.handle(statement_event)
    assert len(reviews(wf)) == 2
    assert "statement_missing" not in {f["code"] for f in reviews(wf)[-1].payload["findings"]}


def test_monitor_cli_loads_project_environment(monkeypatch, tmp_path):
    import sys

    from contracts import registry
    from workflow.monitor import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RAILS")
    (tmp_path / ".env").write_text("RAILS=real\n")
    rails = MemoryRails.from_world((3,), n_clean=0)
    monkeypatch.setattr(registry, "rails", lambda: rails)
    monkeypatch.setattr(sys, "argv", ["monitor", "--once"])
    main()
    assert any(e.type == "workflow_done" for e in rails.events())


def test_monitor_scans_once_daily_and_processes_new_payment_same_day():
    from workflow.monitor import Monitor
    wf = make_workflow(MemoryRails.from_world((3,), n_clean=0), agents=RealAgents(None))
    now = datetime(2026, 4, 22, 12, tzinfo=UTC)
    monitor = Monitor(wf)
    monitor.tick(now)
    count = len(wf.deps.rails.events())
    assert monitor.tick(now) == []
    assert len(wf.deps.rails.events()) == count
    bank = Document(id="bank-arrives", kind="manager_bank", manager_id="keyway",
                    dated=now.date(), data={"payments": [{"invoice_id": "INV-KW-0301",
                        "vendor_id": "v-riogrande", "amount": 1240, "on": "2026-04-22"}]})
    wf.deps.rails.append_event(document_event(bank, now))
    assert monitor.tick(now)
    assert reviews(wf)
    Monitor(make_workflow(wf.deps.rails, agents=RealAgents(None))).tick(
        datetime(2026, 4, 23, 12, tzinfo=UTC))
    scans = [e for e in wf.deps.rails.events() if e.type == "scan_requested"]
    assert len(scans) == 2
