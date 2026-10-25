"""Time-lapse demo of the agent pipeline on its own copy of the books.

The demo runs in a separate Postgres schema copied from the real event log, so speeding up the
calendar, simulated email delivery and resets never touch the real data. Each `next_day`:

    1. the world clock moves one day (a clock_advanced event in the demo schema)
    2. the storyboard adds that day's new evidence (for example a late bank payment)
    3. the chaser is dispatched for every open invoice under a saved agreement
    4. the agents work the queue: Claude is called only where there is a decision to make
    5. reminders queued today are delivered (simulated, receipt recorded) so the ladder can move

Real: the agents, Claude calls, guardrails, queue, traces, both databases.
Simulated: the calendar speed, the storyboard evidence and email delivery.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from contracts.models import Document, Event
from rails.events.ingest import document_event

from . import pipeline

SCHEMA = "demo_live"
START = date(2026, 4, 22)
END = date(2026, 5, 12)
FOCUS = "INV-KW-0301"

# Evidence that arrives on its own during the demo; the agents are not told it is coming.
STORYBOARD = {
    date(2026, 4, 29): [Document(
        id="demo-bank-kw-0301", kind="manager_bank", manager_id="keyway", dated=date(2026, 4, 29),
        text="Keyway operating account, outgoing ACH",
        data={"payments": [{"invoice_id": FOCUS, "vendor_id": "v-riogrande",
                            "payee": "Rio Grande Plumbing LLC", "bank": "First Mesa Bank",
                            "routing": "102000021", "account": "4471029", "amount": 1240.0,
                            "on": "2026-04-29"}]})],
}


def rails():
    from rails.real import RealRails
    return RealRails(SCHEMA)


def _stamp(day: date) -> datetime:
    return datetime.combine(day, time(9), tzinfo=UTC)


def reset(mirror) -> dict:
    """Fresh copy of the books at the start date; clears the queue and the chaser's records."""
    from rails.db import connect
    from rails.real import RealRails
    conn = connect()
    conn.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
    conn.close()
    demo = rails()
    for event in RealRails().store.events():
        demo.append_event(event)
    mirror.call("clear_tasks", [])
    mirror.call("clear_chaser", [])
    return state(demo)


def state(demo) -> dict:
    today = demo.now()
    return {"today": today.isoformat(), "start": START.isoformat(), "end": END.isoformat(),
            "focus": FOCUS, "done": today >= END,
            "storyboard": [{"on": d.isoformat(), "what": f"{doc.kind.value} {doc.id}"}
                           for d, docs in sorted(STORYBOARD.items()) for doc in docs]}


def _deliver(demo, mirror, today: date) -> list[str]:
    from . import connected
    delivered = []
    for case in connected.cases(demo, pipeline.MANAGER):
        for o in case["outbox"]:
            if o["status"] == "queued" and o.get("action_id") and o["kind"] in (
                    "chase_reminder", "escalation"):
                connected.acknowledge(demo, mirror, o["id"], f"simulated-email-{today}",
                                      "demo_mailer", pipeline.MANAGER)
                delivered.append(o["id"])
    return delivered


def run_day(demo, mirror, client=None) -> dict:
    """Run today's work without moving the clock (used for the first day after a reset)."""
    from . import connected
    today = demo.now()
    queued = pipeline.dispatch(demo, mirror, today, since=START)
    processed = pipeline.process(demo, mirror, today, client=client, limit=30)
    cases = connected.reconcile(demo, mirror, pipeline.MANAGER, as_of=today)
    # Paid and handed off: any reminder draft still waiting for the controller is moot.
    from .api import mark_handoffs_reviewed
    mark_handoffs_reviewed(mirror, cases)
    delivered = _deliver(demo, mirror, today)
    return {**state(demo), "queued": queued, "processed": processed, "delivered": delivered}


def next_day(demo, mirror, client=None) -> dict:
    today = demo.now() + timedelta(days=1)
    if today > END:
        return {**state(demo), "processed": [], "note": "demo calendar finished; reset to replay"}
    demo.append_event(Event(id=f"ev-demo-clock-{today}", type="clock_advanced",
                            valid_from=_stamp(today), learned_at=_stamp(today),
                            payload={"as_of": today.isoformat()}))
    arrived = []
    for doc in STORYBOARD.get(today, []):
        demo.append_event(document_event(doc, _stamp(today)))
        arrived.append({"id": doc.id, "kind": doc.kind.value})
    return {**run_day(demo, mirror, client), "arrived": arrived}
