"""Task 1: event -> fixed next steps, checkpointing, replay without duplicates (stand-ins)."""

from datetime import UTC, datetime

import pytest

from contracts.fakes import FakeVerifier
from contracts.models import Event
from workflow.book import WorkflowBook
from workflow.memrails import MemoryRails
from workflow.run import scan_events
from workflow.state import Route
from workflow.steps import Steps
from world import generate

from .conftest import make_workflow


def _ev(type_, **kw):
    when = datetime(2026, 4, 25, tzinfo=UTC)
    return Event(id=f"ev-{type_}", type=type_, valid_from=when, learned_at=when, **kw)


@pytest.mark.parametrize("event,route", [
    (_ev("document_ingested", payload={"kind": "agreement", "data": {}}), Route.agreement),
    (_ev("document_ingested", payload={"kind": "manager_bank", "data": {}}), Route.assess),
    (_ev("document_ingested", payload={"kind": "vendor_notice",
                                       "data": {"status": "bank_change_request"}}), Route.bank_change),
    (_ev("fact_corrected", payload={}), Route.assess),
    (_ev("chase_reply", payload={}), Route.reply),
    (_ev("bank_change_requested", payload={}), Route.bank_change),
    (_ev("scan_requested", payload={}), Route.scan),
    (_ev("funding_review_requested", payload={}), Route.funding_review),
    (_ev("world_meta", payload={}), Route.ignore),
    (_ev("alert_raised", payload={}), Route.ignore),       # never react to our own output
])
def test_routing_is_fixed_by_event_type(event, route):
    assert Steps._route_for(event) == route


@pytest.fixture
def s3():
    return generate(scenarios=(3,), n_clean=10)


def _wf_events(rails):
    return [e for e in rails.events() if e.id.startswith("ev-wf-")]


def test_scenario_3_raises_one_cited_alert_and_starts_chase(s3):
    wf = make_workflow()
    wf.replay(s3.events())
    book = WorkflowBook.load(wf.deps.rails)
    [alert] = book.open_alerts("keyway")
    assert alert.draft.invoice_ids == ["INV-KW-0301"]
    assert all(c.citations for c in alert.draft.claims)
    assert [m.level for m in book.chases["keyway"]] == ["polite"]


def test_invoice_not_alerted_before_due_date(s3):
    wf = make_workflow()
    early = [e for e in s3.events() if e.valid_from.date().isoformat() <= "2026-03-12"]
    wf.replay(early)
    assert not WorkflowBook.load(wf.deps.rails).open_ids


def test_replay_adds_no_duplicates(s3):
    wf = make_workflow()
    wf.replay(s3.events())
    before = _wf_events(wf.deps.rails)
    again = wf.replay(s3.events())
    assert all(r.skipped for r in again)
    assert _wf_events(wf.deps.rails) == before


def test_fresh_workflow_over_same_log_adds_no_duplicates(s3):
    """Restart (new process, empty checkpoints) over an already-processed log."""
    rails = MemoryRails()
    make_workflow(rails).replay(s3.events())
    before = _wf_events(rails)
    make_workflow(rails).replay(s3.events() + scan_events(["keyway"], s3.as_of))
    after = _wf_events(rails)
    assert len([e for e in after if e.type == "alert_raised"]) == 1
    assert len(after) == len(before) + 1          # only the new scan's done-marker


class CrashOnce(FakeVerifier):
    def __init__(self):
        self.calls = 0

    def verify(self, draft, evidence):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("verifier down")
        return super().verify(draft, evidence)


def test_crashed_run_resumes_from_checkpoint(s3):
    wf = make_workflow(verifier=CrashOnce())
    events = sorted(s3.events(), key=lambda e: (e.learned_at, e.id))
    stmt = next(e for e in events if e.id == "ev-doc-doc-stmt-kw-2026-03")
    wf.replay(events[: events.index(stmt)])
    with pytest.raises(RuntimeError):
        wf.ingest(stmt)
    r = wf.handle(stmt)                     # resumes at `verify`, not from the start
    assert r.raised and not r.skipped
    assert "route" not in " ".join(r.trace[-4:])
    assert len(WorkflowBook.load(wf.deps.rails).open_ids) == 1


def test_scan_reassesses_a_whole_manager(s3):
    wf = make_workflow()
    wf.replay(s3.events())
    [r] = wf.replay(scan_events(["keyway"], s3.as_of))
    assert r.route == "scan"
    assert any("INV-KW-0301: delayed" in t for t in r.trace)
    assert any("already open" in t for t in r.trace)
