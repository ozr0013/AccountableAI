from datetime import UTC, datetime, timedelta

import pytest

from contracts.models import LedgerLine
from rails.real import RealRails
from workflow.cases import CaseConflict, CaseStore
from workflow.memrails import MemoryRails
from workflow.scheduler import LeaseConflict, Scheduler

NOW = datetime(2026, 10, 24, tzinfo=UTC)
LINE = LedgerLine(invoice_id="i", manager_id="m", vendor_id="v", amount=10)


@pytest.fixture(params=["memory", "postgres"])
def rails(request):
    if request.param == "memory":
        return MemoryRails()
    return RealRails(schema=request.getfixturevalue("schema"))


def test_case_restart_cas_scope_and_audit(rails):
    store = CaseStore(rails)
    case = store.ensure(LINE, NOW)
    assert store.ensure(LINE, NOW).id == case.id
    updated = store.update(case.id, 1, NOW, state="awaiting_statement", plan=["read"])
    assert updated.version == 2
    assert CaseStore(rails).get(case.id) == updated
    with pytest.raises(CaseConflict):
        store.update(case.id, 1, NOW, summary="stale")
    with pytest.raises(ValueError):
        store.update(case.id, 2, NOW, vendor_id="wrong")
    with pytest.raises(ValueError):
        store.update(case.id, 2, NOW, state="invented")
    events = [e for e in rails.events() if e.type == "case_changed"]
    assert len(events) == 2
    assert store.record(case.id, "observed", {"a": 1}, NOW, "one") == store.record(
        case.id, "observed", {"a": 1}, NOW, "one"
    )
    with pytest.raises(ValueError):
        store.record(case.id, "observed", {"a": 2}, NOW, "one")


def test_scheduler_recovery_idempotency_and_ownership(rails):
    case = CaseStore(rails).ensure(LINE, NOW)
    scheduler = Scheduler(rails)
    job = scheduler.schedule(case.id, NOW, "wake", {"x": 1})
    assert scheduler.schedule(case.id, NOW, "wake", {"x": 1}) == job
    assert scheduler.claim("a", NOW)["attempts"] == 1
    assert Scheduler(rails).claim("b", NOW) is None
    with pytest.raises(LeaseConflict):
        scheduler.complete(job, "b", NOW)
    later = NOW + timedelta(seconds=121)
    assert Scheduler(rails).claim("b", later)["attempts"] == 2
    with pytest.raises(LeaseConflict):
        scheduler.complete(job, "a", later)
    scheduler.fail(job, "b", "retry", later + timedelta(seconds=2), now=later)
    assert scheduler.claim("c", later) is None
    scheduler.claim("c", later + timedelta(seconds=2))
    scheduler.complete(job, "c", later + timedelta(seconds=3))
    assert scheduler.list()[0]["status"] == "completed"


def test_two_connections_skip_locked_and_expired_completion(schema):
    a, b = RealRails(schema=schema), RealRails(schema=schema)
    case = CaseStore(a).ensure(LINE, NOW)
    first, second = Scheduler(a), Scheduler(b)
    j1 = first.schedule(case.id, NOW, "one")
    j2 = first.schedule(case.id, NOW, "two")
    with a.store.conn.transaction():
        a.store.conn.execute("SELECT id FROM scheduled_jobs WHERE id=%s FOR UPDATE", (j1,))
        assert second.claim("b", NOW)["id"] == j2
    assert first.claim("a", NOW)["id"] == j1
    with pytest.raises(LeaseConflict):
        first.complete(j1, "a", NOW + timedelta(seconds=121))
    assert second.claim("recover", NOW + timedelta(seconds=121)) is not None


def test_restart_from_event_log_preserves_cases_and_jobs():
    rails = MemoryRails()
    case = CaseStore(rails).ensure(LINE, NOW)
    scheduler = Scheduler(rails)
    scheduler.schedule(case.id, NOW, "wake")
    scheduler.claim("original", NOW)
    restored = MemoryRails(rails.events())
    assert CaseStore(restored).get(case.id) == case
    assert Scheduler(restored).claim("other", NOW) is None
    assert Scheduler(restored).claim("other", NOW + timedelta(seconds=121))["attempts"] == 2


def test_cancel_and_timezone_validation(rails):
    case = CaseStore(rails).ensure(LINE, NOW)
    scheduler = Scheduler(rails)
    scheduler.schedule(case.id, NOW, "wake")
    scheduler.claim("worker", NOW)
    scheduler.cancel(case.id)
    with pytest.raises(LeaseConflict):
        scheduler.complete(scheduler.list()[0]["id"], "worker", NOW)
    assert scheduler.claim("other", NOW + timedelta(days=2)) is None
    with pytest.raises(ValueError):
        scheduler.schedule(case.id, NOW.replace(tzinfo=None), "bad")
    with pytest.raises(ValueError):
        CaseStore(rails).update(case.id, 1, NOW, next_wake_at=NOW.replace(tzinfo=None))


def test_case_update_rolls_back_projection_when_audit_append_fails(schema, monkeypatch):
    rails = RealRails(schema=schema)
    store = CaseStore(rails)
    case = store.ensure(LINE, NOW)

    def broken(event):
        raise RuntimeError("simulated interrupted audit")

    monkeypatch.setattr(rails.store, "append", broken)
    with pytest.raises(RuntimeError):
        store.update(case.id, 1, NOW, summary="must roll back")
    assert store.get(case.id).version == 1


def test_two_connections_stale_case_version(schema):
    first, second = CaseStore(RealRails(schema=schema)), CaseStore(RealRails(schema=schema))
    case = first.ensure(LINE, NOW)
    second.update(case.id, 1, NOW, summary="second connection")
    with pytest.raises(CaseConflict):
        first.update(case.id, 1, NOW, summary="lost update")
    assert first.get(case.id).summary == "second connection"


def test_expired_lease_cannot_retry(rails):
    case = CaseStore(rails).ensure(LINE, NOW)
    scheduler = Scheduler(rails)
    job = scheduler.schedule(case.id, NOW, "wake")
    scheduler.claim("a", NOW)
    later = NOW + timedelta(seconds=121)
    with pytest.raises(LeaseConflict):
        scheduler.fail(job, "a", "late", later + timedelta(seconds=10), now=later)


def test_resolved_cases_require_explicit_evidence_reopen(rails):
    store = CaseStore(rails)
    case = store.ensure(LINE, NOW)
    case = store.update(case.id, case.version, NOW, state="resolved")
    with pytest.raises(ValueError):
        store.update(case.id, case.version, NOW, state="awaiting_human")
    with pytest.raises(ValueError):
        store.update(case.id, case.version, NOW, state="monitoring")
    reopened = store.update(
        case.id, case.version, NOW, state="monitoring", last_observation={"new_evidence": ["doc-2"]}
    )
    assert reopened.state == "monitoring"
