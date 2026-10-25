"""Time-lapse demo of the agent pipeline on its own copy of the books.

The demo runs in a separate Postgres schema copied from the real event log, so speeding up the
calendar, simulated email delivery and resets never touch the real data. Each `next_day`:

    1. the world clock moves one day (a clock_advanced event in the demo schema)
    2. the storyboard adds that day's new evidence (for example a late bank payment)
    3. the chaser is dispatched for every open invoice under a saved agreement
    4. the agents work the queue: Claude is called only where there is a decision to make
    5. reminders queued today are delivered (simulated, receipt recorded) so the ladder can move

Real: the agents, Claude calls, guardrails, queue, traces, both databases, and with
DEMO_EMAIL=on the emails (SMTP_* settings; WORKFLOW_MANAGEMENT_EMAIL / WORKFLOW_OWNER_EMAIL stand in
for Keyway and Oakline).
Simulated: the calendar speed, the storyboard evidence, and email delivery when DEMO_EMAIL is off.
"""
from __future__ import annotations

import os
from datetime import UTC, date, datetime, time, timedelta

from contracts.models import Document, Event
from rails.events.ingest import document_event

from . import pipeline

SCHEMA = "demo_live"
START = date(2026, 4, 22)
# DEMO_END shortens the calendar for a quick showing; the INV-KW-0301 story is complete by May 1.
END = date.fromisoformat(os.getenv("DEMO_END", "2026-05-12"))
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


_RAILS = None


def rails():
    """One shared connection to the demo copy (a new one per request would exhaust Postgres)."""
    global _RAILS
    if _RAILS is None:
        from rails.real import RealRails
        _RAILS = RealRails(SCHEMA)
    return _RAILS


def _forget():
    global _RAILS
    if _RAILS is not None and _RAILS.store.conn is not None:
        try:
            _RAILS.store.conn.close()
        except Exception:  # noqa: BLE001 - closing a dead connection
            pass
    _RAILS = None


def _stamp(day: date) -> datetime:
    return datetime.combine(day, time(9), tzinfo=UTC)


def reset(mirror) -> dict:
    """Fresh copy of the books at the start date; clears the queue and the chaser's records."""
    from rails.real import RealRails, books_backend

    from . import books
    _forget()
    books.forget(SCHEMA)
    if books_backend() == "spacetime":
        demo = rails()
        demo.store.copy_from("public")    # one SpacetimeDB transaction, same order as the original
    else:
        from rails.db import connect
        conn = connect()
        conn.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        conn.close()
        demo = rails()
        for event in RealRails().store.events():
            demo.append_event(event)
    mirror.call("clear_tasks", [])
    mirror.call("clear_chaser", [])
    _arrive(demo, mirror, START)          # the first morning's paperwork
    return state(demo)


_FEED: dict | None = None


def feed(demo, mirror) -> dict:
    """The seeded daily paperwork (chaser_agent/demo_feed.py), built once from the books."""
    global _FEED
    if _FEED is None:
        # Built from the original books, never the demo copy: the copy already holds earlier
        # days' paperwork, which would change the vendors' usual amounts and so the whole month.
        from rails.real import RealRails

        from . import demo_feed
        _FEED = demo_feed.build(demo_feed.vendors(RealRails(), mirror), START, END, pipeline.MANAGER)
    return _FEED


def _arrive(demo, mirror, today: date) -> list[dict]:
    """File today's documents in the books: the storyboard plus the seeded feed."""
    from . import runtime
    arrived = []
    for doc in STORYBOARD.get(today, []) + feed(demo, mirror).get(today, []):
        demo.append_event(document_event(doc, _stamp(today)))
        arrived.append({"id": doc.id, "kind": doc.kind.value, "lines": doc.data.get("lines")})
        runtime.emit({"type": "evidence", "day": today.isoformat(), "document": doc.id,
                      "kind": doc.kind.value, "text": doc.text[:240]})
    return arrived


def state(demo) -> dict:
    today = demo.now()
    return {"today": today.isoformat(), "start": START.isoformat(), "end": END.isoformat(),
            "focus": FOCUS, "done": today >= END,
            "storyboard": [{"on": d.isoformat(), "what": f"{doc.kind.value} {doc.id}"}
                           for d, docs in sorted(STORYBOARD.items()) for doc in docs]}


MAIL_KINDS = ("chase_reminder", "escalation", "statement_notice")


def transport_subject(row) -> str:
    from .transport import _subject
    return _subject(row)


def _flag_failed_delivery(demo, mirror, today: date, errors: list[dict]) -> list[str]:
    """A message that could not be confirmed as sent is never retried automatically (it might
    have gone out), so the ladder would wait on it forever. Put it in front of the controller."""
    from . import api, connected, daily
    if not errors:
        return []
    where = {o["id"]: (c, o) for c in connected.cases(demo, pipeline.MANAGER) for o in c["outbox"]}
    known = {h["action_id"] for h in api.history(mirror)}
    flagged = []
    for err in errors:
        hit = where.get(err.get("outbox_id"))
        if hit is None:
            continue
        case, row = hit
        flag = daily.Decision(
            invoice_id=case["invoice_id"], vendor_name=case.get("vendor") or "", action="flag",
            level="delivery_failed", status="needs_controller", attempt=0,
            reason=f"The {row['kind'].replace('_', ' ')} for {case['invoice_id']} could not be confirmed "
                   f"as sent ({err['error']}); it is not retried automatically. Check the mailbox and "
                   f"record the delivery, or resend by hand.",
            message=row["body"][:600])
        if flag.action_id() in known:
            continue
        pipeline._record_chase(mirror, today)(flag, flag.reason)
        flagged.append(flag.action_id())
    return flagged


def _deliver(demo, mirror, today: date) -> list[str]:
    import os

    from . import connected, runtime
    if os.getenv("DEMO_EMAIL", "off").lower() == "on":
        from . import transport
        sent = transport.dispatch(demo, mirror, pipeline.MANAGER, enabled=True, kinds=MAIL_KINDS,
                                  as_of=today)
        for item in sent["sent"]:
            runtime.emit({"type": "email_sent", "day": today.isoformat(), **item})
        for err in sent["errors"]:
            runtime.emit({"type": "email_failed", "day": today.isoformat(), **err})
        _flag_failed_delivery(demo, mirror, today, sent["errors"])
        return sent["delivered"]
    delivered = []
    for case in connected.cases(demo, pipeline.MANAGER):
        for o in case["outbox"]:
            if o["status"] == "queued" and o["kind"] in MAIL_KINDS:
                connected.acknowledge(demo, mirror, o["id"], f"simulated-email-{today}",
                                      "demo_mailer", pipeline.MANAGER)
                delivered.append(o["id"])
                runtime.emit({"type": "email_sent", "day": today.isoformat(), "outbox_id": o["id"],
                              "invoice_id": case["invoice_id"], "kind": o["kind"],
                              "subject": transport_subject(o), "simulated": True})
    return delivered


PAYMENT_FINDINGS = {"partial_payment", "overpayment", "wrong_vendor", "late_payment"}
STATEMENT_FINDINGS = {"statement_mismatch", "statement_wrong_vendor", "statement_predates_payment"}


def _statement_reviews(demo, mirror, today: date, arrived: list[dict]) -> list[str]:
    """A statement arrived: the anomaly agent reviews each paid invoice on it that the checks
    flag. One it already reviewed is reviewed again only if the statement itself disagrees.
    Unpaid invoices the statement calls paid stay with the chaser, which sees the claim."""
    from . import connected
    lines = {l["invoice_id"] for a in arrived if a["kind"] == "manager_statement"
             for l in (a.get("lines") or [])}
    if not lines:
        return []
    reviewed = {t["invoice_id"] for t in pipeline.tasks(mirror, agent="anomaly")}
    queued = []
    for case in connected.cases(demo, pipeline.MANAGER):
        codes = {f["code"] for f in case["findings"]}
        if case["invoice_id"] not in lines or not codes & (PAYMENT_FINDINGS | STATEMENT_FINDINGS):
            continue
        if case["invoice_id"] in reviewed and not codes & STATEMENT_FINDINGS:
            continue
        queued.append(pipeline.enqueue(
            mirror, "anomaly", case["invoice_id"],
            "Keyway's new statement covers this invoice and the checks found: "
            + "; ".join(f["message"] for f in case["findings"]), {}, today, "statement_arrived"))
    return queued


def run_day(demo, mirror, client=None, arrived=()) -> dict:
    """Run today's work without moving the clock (used for the first day after a reset)."""
    from . import connected, runtime
    today = demo.now()
    runtime.emit({"type": "day_started", "day": today.isoformat()})
    # The books as of this morning, so the agents see what arrived overnight.
    connected.reconcile(demo, mirror, pipeline.MANAGER, as_of=today)
    reviews = _statement_reviews(demo, mirror, today, list(arrived))
    sweep = []
    if today.weekday() < 5:              # the investigator works weekdays, before the chaser
        sweep = [pipeline.enqueue(mirror, "investigator", "portfolio",
                                  "daily sweep of all invoices and bank payments", {}, today,
                                  "daily_schedule")]
    queued = sweep + pipeline.dispatch(demo, mirror, today, since=START) + reviews
    processed = pipeline.process(demo, mirror, today, client=client, limit=80)
    cases = connected.reconcile(demo, mirror, pipeline.MANAGER, as_of=today)
    # Paid and handed off: any reminder draft still waiting for the controller is moot.
    from .api import mark_handoffs_reviewed
    mark_handoffs_reviewed(mirror, cases)
    delivered = _deliver(demo, mirror, today)
    runtime.emit({"type": "day_done", "day": today.isoformat(), "processed": len(processed)})
    return {**state(demo), "queued": queued, "processed": processed, "delivered": delivered}


def next_day(demo, mirror, client=None) -> dict:
    today = demo.now() + timedelta(days=1)
    if today > END:
        return {**state(demo), "processed": [], "note": "demo calendar finished; reset to replay"}
    demo.append_event(Event(id=f"ev-demo-clock-{today}", type="clock_advanced",
                            valid_from=_stamp(today), learned_at=_stamp(today),
                            payload={"as_of": today.isoformat()}))
    arrived = _arrive(demo, mirror, today)
    return {**run_day(demo, mirror, client, arrived),
            "arrived": [{"id": a["id"], "kind": a["kind"]} for a in arrived]}
