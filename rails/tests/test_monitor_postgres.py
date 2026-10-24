"""Chaser lifecycle against real PostgreSQL, isolated by the schema fixture."""

from datetime import UTC, datetime

from agents import RealAgents
from contracts.fakes import FakeApprovals, FakeVerifier
from contracts.models import Document
from rails.events.ingest import document_event
from rails.real import RealRails
from workflow import Deps, Workflow
from workflow.monitor import Monitor
from world import generate


def test_monitor_persists_reviews_and_resumes_without_duplicates(schema, monkeypatch):
    monkeypatch.setenv("WORKFLOW_CHECKPOINT", "memory")
    rails = RealRails(schema=schema)
    rails.load(generate(13, (3,), 0).events())

    def monitor(database):
        return Monitor(Workflow(Deps(database, RealAgents(None), FakeVerifier(), FakeApprovals())))

    now = datetime(2026, 4, 22, 12, tzinfo=UTC)
    worker = monitor(rails)
    assert worker.tick(now)
    bank = Document(id="postgres-wrong-vendor", kind="manager_bank", manager_id="keyway",
                    dated=now.date(), data={"payments": [{"invoice_id": "INV-KW-0301",
                        "vendor_id": "v-other", "amount": 1240, "on": "2026-04-22"}]})
    rails.append_event(document_event(bank, now))
    worker.tick(now)
    [review] = rails.store.events(type="payment_reviewed")
    assert "wrong_vendor" in {f["code"] for f in review.payload["findings"]}
    assert rails.ledger("keyway")[0].paid_amount == 0
    assert len(rails.store.events(type="finance_update_queued")) == 1
    assert len(rails.store.events(type="statement_request_drafted")) == 1

    # A new connection and workflow recover from PostgreSQL, not Python object state.
    resumed = RealRails(schema=schema)
    count = len(resumed.events())
    assert monitor(resumed).tick(now) == []
    assert len(resumed.events()) == count
    monitor(resumed).tick(datetime(2026, 4, 23, 12, tzinfo=UTC))
    assert len(resumed.store.events(type="scan_requested")) == 2
    assert len(resumed.store.events(type="payment_reviewed")) == 1
    resumed.store.conn.close()
    rails.store.conn.close()
