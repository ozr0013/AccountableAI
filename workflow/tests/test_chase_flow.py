"""Chasing through the workflow: reminders on schedule, "paid" without proof keeps the alert."""

from datetime import UTC, datetime

import pytest

from agents import RealAgents
from contracts.models import Event
from workflow.book import WorkflowBook
from workflow.memrails import MemoryRails
from workflow.run import scan_events
from world import generate

from .conftest import make_workflow

ALERT_S3 = "INV-KW-0301"


@pytest.fixture
def wf():
    w = generate(scenarios=(3,), n_clean=10)
    rails = MemoryRails()
    wf = make_workflow(rails, agents=RealAgents(None))
    wf.replay(w.events())
    return wf


def _reply(text, day=26):
    when = datetime(2026, 4, day, 12, tzinfo=UTC)
    return Event(id=f"ev-reply-{day}", type="chase_reply", manager_id="keyway",
                 invoice_id=ALERT_S3, valid_from=when, learned_at=when,
                 payload={"id": f"ev-reply-{day}", "manager_id": "keyway",
                          "invoice_ids": [ALERT_S3], "text": text, "claims_paid": True,
                          "proof": []})


def _open(wf):
    return {i for a in WorkflowBook.load(wf.deps.rails).open_alerts() for i in a.draft.invoice_ids}


def test_first_reminder_uses_real_names(wf):
    [m] = WorkflowBook.load(wf.deps.rails).chases["keyway"]
    assert m.level == "polite" and "Keyway Property Management" in m.body
    assert "Rio Grande Plumbing" in m.body


def test_follow_ups_escalate_only_on_schedule(wf):
    from datetime import date
    wf.replay(scan_events(["keyway"], date(2026, 3, 30)))              # 0 days later: too soon
    wf.replay(scan_events(["keyway"], date(2026, 4, 30)))              # firm
    wf.replay(scan_events(["keyway"], date(2026, 5, 2)))               # too soon again
    wf.replay(scan_events(["keyway"], date(2026, 5, 6)))               # escalation
    assert [m.level for m in WorkflowBook.load(wf.deps.rails).chases["keyway"]] == [
        "polite", "firm", "escalation"]


def test_paid_reply_without_proof_holds_position(wf):
    [r] = wf.replay([_reply("We paid the plumber already.")])
    assert any("holding position" in t for t in r.trace)
    assert ALERT_S3 in _open(wf) and not r.withdrawn
    [alert] = [a for a in WorkflowBook.load(wf.deps.rails).open_alerts()
               if a.draft.invoice_ids == [ALERT_S3]]
    cited = {c.source_id for cl in alert.draft.claims for c in cl.citations}
    assert any(s.startswith("ev-reply-26") for s in cited)          # the reply is cited


def test_late_bank_proof_turns_unpaid_alert_into_paid_late_and_stops_chasing(wf):
    when = datetime(2026, 4, 27, tzinfo=UTC)
    from contracts.models import Document
    from rails.events.ingest import document_event
    bank = Document(id="doc-bank-kw-proof", kind="manager_bank", manager_id="keyway",
                    dated=when.date(), text="04/27 Rio Grande Plumbing $1,240.00",
                    data={"payments": [{"invoice_id": ALERT_S3, "vendor_id": "v-riogrande",
                                        "amount": 1240.0, "on": "2026-04-27"}]})
    wf.replay([document_event(bank, when)])
    book = WorkflowBook.load(wf.deps.rails)
    [alert] = [a for a in book.open_alerts() if a.draft.invoice_ids == [ALERT_S3]]
    assert alert.draft.amount == 0 and "paid 45 days late" in alert.draft.headline
    wf.replay(scan_events(["keyway"], when.date().replace(day=30)))
    assert len(WorkflowBook.load(wf.deps.rails).chases["keyway"]) == 1     # nothing owed now
