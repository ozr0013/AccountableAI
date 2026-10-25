"""Chaser agent as a JSON API.

    python -m chaser_agent.api            # http://127.0.0.1:8773/docs

Triggers
    POST /chaser/run           run the daily check now (default as_of: the world's today)
    CHASER_DAILY=on            the API also runs it by itself every CHASER_INTERVAL_S seconds
                               (default 86400, once a day)
Reads
    GET  /chaser/actions       every recorded action (reminders, escalations, hand-offs, holds)
Controller (Authorization: Bearer $CONTROLLER_API_TOKEN)
    POST /chaser/actions/{action_id}/decide   {"approve": true|false, "note": "..."}
         approves or declines a firm / escalation reminder, or an escalation and its hold
    GET  /chaser/patterns      how regularly each vendor is paid late (worst first)
Replies
    POST /chaser/reply         a manager's reply: Claude reads it, code checks the proof
Questions
    POST /chaser/ask           {"question": "..."}: Claude answers with read-only tools over
                               invoices, lateness, agreement rules and chaser actions

In:  invoices and bank payments from PostgreSQL (rails), each vendor's rules from SpacetimeDB
     `agreement_rule`, and earlier actions from `chase_action`.
Out: one decision per invoice; reminders, escalations, hand-offs and holds are saved to
     `chase_action`. The polite reminder is queued automatically; firm and escalation reminders
     and escalations wait for the controller. Queued messages go to the outbox; no transport is
     configured, so nothing is actually emailed.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

import psycopg
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import daily

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
log = logging.getLogger(__name__)
MANAGER = "keyway"

_lock = threading.RLock()
_last: dict = {}


def worker_enabled() -> bool:
    return os.getenv("CONNECTED_WORKFLOW", "on").lower() == "on" or os.getenv("CHASER_DAILY", "off").lower() == "on"


@contextmanager
def workflow_lock(rails):
    """Serialize processes as well as threads; events remain the durable work source."""
    conn = getattr(getattr(rails, 'store', None), 'conn', None)
    locked = isinstance(conn, psycopg.Connection)
    if locked and not conn.execute('SELECT pg_try_advisory_lock(73920126)').fetchone()[0]:
        raise HTTPException(409, 'Workflow is processing another event; retry shortly.')
    try:
        yield
    finally:
        if locked:
            conn.execute('SELECT pg_advisory_unlock(73920126)')


@asynccontextmanager
async def _lifespan(_app):
    stop = threading.Event()
    if worker_enabled():
        interval = max(1.0, float(os.getenv("CHASER_INTERVAL_S", "15")))
        threading.Thread(target=_daily_loop, args=(interval, stop), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="Chaser agent", description=__doc__ or "", lifespan=_lifespan)

async def unavailable(_request, exc):
    log.error("Chaser data dependency failed: %s", type(exc).__name__)
    return JSONResponse(status_code=502, content={
        "detail": "Agent data service unavailable; check PostgreSQL and SpacetimeDB."})


for error_type in (OSError, psycopg.Error, subprocess.SubprocessError, RuntimeError):
    app.add_exception_handler(error_type, unavailable)



def _opt(v):
    return {"none": []} if v is None else {"some": v}


def _mirror():
    from rails.live.spacetime import SpacetimeMirror
    return SpacetimeMirror()


def _rails():
    from rails.real import RealRails
    return RealRails()


@contextmanager
def rail_scope(rails=None):
    owned = rails is None
    rails = _rails() if owned else rails
    try:
        yield rails
    finally:
        conn = getattr(getattr(rails, "store", None), "conn", None)
        if owned and conn is not None:
            conn.close()


def rails_dependency():
    with rail_scope() as rails:
        yield rails


RailsDep = Annotated[Any, Depends(rails_dependency)]


def data_tools(rails, mirror):
    from .assistant import DataTools
    return DataTools(rails, mirror, lambda: delivery_history(rails, history(mirror)), rails.now(), MANAGER)


def delivery_history(rails, rows):
    """Queue/approval are not delivery. Cadence starts at a persisted delivery receipt."""
    from .connected import cases
    receipts = {o['action_id']: o for c in cases(rails, MANAGER) for o in c['outbox'] if o.get('action_id')}
    projected = []
    for row in rows:
        item = dict(row)
        if item['action'] == 'reminder':
            receipt = receipts.get(item['action_id'])
            if receipt and receipt['status'] == 'cancelled':
                item['status'] = 'superseded'
            elif receipt and receipt['status'] == 'delivered':
                item['as_of'] = receipt.get('delivered_on', receipt['created_on'])
            elif item['status'] in ('queued', 'approved'):
                item['status'] = 'awaiting_delivery'
        projected.append(item)
    return projected


def history(mirror) -> list[dict]:
    """Recorded actions with the controller's decision applied to their status."""
    decided = {d["action_id"]: d for d in mirror.query("SELECT * FROM chase_decision")}
    out = []
    for row in mirror.query("SELECT * FROM chase_action"):
        d = decided.get(row["action_id"])
        out.append({**row, "status": d["status"], "decided_by": d["decided_by"],
                    "decision_note": d["note"]} if d else row)
    return out


def run_once(as_of: date | None = None, rails=None, mirror=None) -> dict:
    with _lock, rail_scope(rails) as active_rails, workflow_lock(active_rails):
        return _run_locked(as_of, active_rails, mirror)


def _run_locked(as_of, active_rails, mirror):
    from .connected import cases, reconcile
    from .transport import dispatch
    # Called only under the thread/process coordinator lock.
    mirror = mirror or _mirror()
    today = as_of or active_rails.now()
    # Invalidate old outbox approvals before deciding whether a pending chase blocks progress.
    reconcile(active_rails, mirror, MANAGER, as_of=today)
    previous = delivery_history(active_rails, history(mirror))
    recorded_ids = {row["action_id"] for row in previous}
    decisions = daily.run(active_rails, mirror.query("SELECT * FROM agreement_rule"),
                          previous, today, MANAGER)
    saved = 0
    for d in decisions:
        if not d.recorded or d.action_id() in recorded_ids:
            continue
        mirror.call("record_chase", [
            d.action_id(), d.invoice_id, d.vendor_name, d.action, _opt(d.level),
            _opt(d.due_date), d.days_overdue, float(d.amount_owed), _opt(d.message), d.reason,
            _opt(d.rule_id), _opt(d.rule_quote), d.status, today.isoformat()])
        saved += 1
    counts: dict[str, int] = {}
    for d in decisions:
        counts[d.action] = counts.get(d.action, 0) + 1
    case_rows = reconcile(active_rails, mirror, MANAGER, as_of=today)
    mark_handoffs_reviewed(mirror, case_rows)
    delivery = dispatch(active_rails, mirror, MANAGER)
    if delivery['delivered']:
        case_rows = cases(active_rails, MANAGER)
    _last.update(as_of=today.isoformat(), counts=counts, saved=saved, error=None, delivery=delivery)
    return {"as_of": today.isoformat(), "counts": counts, "saved": saved,
            "decisions": [d.to_dict() for d in decisions if d.action != "skip"], "cases": case_rows}

PAYMENT_STATES = {"needs_controller", "awaiting_statement", "closed"}


def mark_handoffs_reviewed(mirror, case_rows) -> int:
    """Close the loop on the chaser's hand-off: once the anomaly checks have produced a case
    verdict for a paid invoice, the hand-off row is marked reviewed with that verdict and any
    undecided reminder draft for that invoice is cancelled."""
    cases_by_invoice = {c["invoice_id"]: c for c in case_rows}
    marked = 0
    for row in history(mirror):
        case = cases_by_invoice.get(row["invoice_id"])
        if case is None or case["state"] not in PAYMENT_STATES:
            continue
        if row["action"] in ("reminder", "escalate") and row["status"] in ("needs_approval",
                                                                          "needs_controller"):
            # Payment arrived: an undecided chase draft is moot; the anomaly review replaces it.
            mirror.call("decide_chase", [row["action_id"], "cancelled", "anomaly_checks",
                                         _opt("payment evidence arrived; see anomaly review")])
            continue
        if row["action"] != "handoff_anomaly" or row["status"] != "recorded":
            continue
        codes = ", ".join(f["code"] for f in case["findings"]) or "no findings"
        mirror.call("decide_chase", [row["action_id"], "reviewed", "anomaly_checks",
                                     _opt(f"case {case['state']}: {codes}")])
        marked += 1
    return marked


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model_available": bool(os.getenv("ANTHROPIC_API_KEY")),
            "daily": os.getenv("CHASER_DAILY", "off"), "connected_worker": worker_enabled(),
            "last_run": _last or None}


@app.post("/chaser/run")
def run(rails: RailsDep,
        as_of: Annotated[date | None, Query(description="Persist checks through world today; use preview for future dates")] = None):
    if as_of is not None and as_of != rails.now():
        raise HTTPException(422, "Dates other than the world clock are preview-only; saved checks use the current world date.")
    return run_once(as_of, rails=rails)


@app.get("/chaser/actions")
def actions(invoice_id: str | None = None) -> list[dict]:
    rows = history(_mirror())
    return sorted((r for r in rows if invoice_id in (None, r["invoice_id"])),
                  key=lambda r: (r["as_of"], r["invoice_id"]))


@app.get("/chaser/patterns")
def patterns(rails: RailsDep, vendor: str | None = None) -> list[dict]:
    from .patterns import vendor_patterns
    rows = vendor_patterns(rails, _mirror().query("SELECT * FROM agreement_rule"), rails.now(),
                           MANAGER)
    return [r for r in rows if not vendor or vendor.lower() in r["vendor"].lower()]


@app.get("/chaser/invoices")
def invoices(rails: RailsDep, status: Literal["all", "overdue", "unpaid", "paid", "not_due"] = "all",
             vendor: Annotated[str | None, Query(max_length=200)] = None,
             limit: Annotated[int, Query(ge=1, le=50)] = 50):
    return {"as_of": rails.now().isoformat(),
            **data_tools(rails, _mirror()).list_invoices(status, vendor, limit)}


@app.get("/chaser/invoices/{invoice_id}")
def invoice(invoice_id: str, rails: RailsDep):
    result = data_tools(rails, _mirror()).get_invoice(invoice_id)
    if "error" in result:
        raise HTTPException(404, "Unknown invoice")
    return result


@app.post("/chaser/preview")
def preview(rails: RailsDep, as_of: date | None = None):
    today = as_of or rails.now()
    mirror = _mirror()
    decisions = daily.run(rails, mirror.query("SELECT * FROM agreement_rule"),
                          delivery_history(rails, history(mirror)), today, MANAGER)
    return {"as_of": today.isoformat(),
            "decisions": [d.to_dict() for d in decisions if d.action != "skip"]}


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=2000)


@app.post("/chaser/ask")
def ask(body: AskIn, rails: RailsDep) -> dict:
    """Claude picks read-only tools to answer; the response lists every tool it called."""
    import anthropic

    from .assistant import DataTools
    from .assistant import ask as run_ask
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(400, "needs ANTHROPIC_API_KEY")
    mirror = _mirror()
    tools = DataTools(rails, mirror, lambda: delivery_history(rails, history(mirror)), rails.now(), MANAGER)
    try:
        return {"question": body.question, **run_ask(body.question, tools)}
    except anthropic.RateLimitError as exc:
        raise HTTPException(429, "Claude rate limit; try again shortly") from exc
    except anthropic.APIStatusError as exc:
        raise HTTPException(502, f"Claude API error {exc.status_code}") from exc
    except anthropic.APIConnectionError as exc:
        raise HTTPException(502, "could not reach Claude") from exc


class ReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invoice_id: str = Field(pattern=r"^INV-[A-Z]{2}-\d{3,4}$")
    text: str = Field(min_length=1, max_length=4000)


@app.post("/chaser/reply")
def reply(body: ReplyIn, rails: RailsDep) -> dict:
    """Claude reads the manager's reply; only a bank record or vendor confirmation is proof."""
    from agents.chaser import ChaserAgent
    from agents.llm import default_llm
    docs = rails.documents(MANAGER, body.invoice_id)
    if not any(d.data.get("invoice_id") == body.invoice_id for d in docs):
        raise HTTPException(404, "unknown invoice")
    llm = default_llm()
    reading = ChaserAgent(llm).read_reply(body.text, MANAGER, docs, [body.invoice_id])
    if not reading.claims_paid:
        verdict = "reply does not claim payment; keep chasing on schedule"
    elif reading.proof:
        verdict = "payment claim backed by " + ", ".join(c.source_id for c in reading.proof)
    else:
        verdict = "holding position: the reply claims payment but no bank record shows it"
    _mirror().call("record_chase", [
        reading.id, body.invoice_id, "", "reply_read", _opt(None), _opt(None), 0, 0.0,
        _opt(body.text), verdict, _opt(None), _opt(None), "recorded", rails.now().isoformat()])
    run_once(rails=rails)
    return {"invoice_id": body.invoice_id, "claims_paid": reading.claims_paid,
            "proof": [c.source_id for c in reading.proof], "verdict": verdict,
            "read_by": "claude" if llm else "rules"}


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool
    note: str | None = Field(default=None, max_length=1000)


def _controller(authorization: str | None = Header(default=None)) -> str:
    from agreement_agent.api import controller
    return controller(authorization)


@app.post("/chaser/actions/{action_id}/decide")
def decide(action_id: str, body: DecideIn, by: Annotated[str, Depends(_controller)],
           rails: RailsDep) -> dict:
    with _lock, workflow_lock(rails):
        return _decide_locked(action_id, body, by, rails)


def _decide_locked(action_id, body, by, rails):
    mirror = _mirror()
    row = next((r for r in history(mirror) if r["action_id"] == action_id), None)
    if row is None:
        raise HTTPException(404, "unknown chaser action")
    if row["status"] not in ("needs_approval", "needs_controller"):
        raise HTTPException(409, f"action is {row['status']}; nothing to decide")
    if body.approve:
        if date.fromisoformat(row["as_of"]) > rails.now():
            raise HTTPException(409, "This draft is dated in the future; preview future dates without approving them.")
        line = next((line for line in rails.ledger(MANAGER)
                     if line.invoice_id == row["invoice_id"]), None)
        if line is None or line.paid_amount > 0:
            raise HTTPException(409, "Payment evidence changed; this draft cannot be approved. Review the anomaly handoff.")
        current = next((d for d in daily.run(rails, mirror.query("SELECT * FROM agreement_rule"),
                       delivery_history(rails, [h for h in history(mirror) if h["action_id"] != action_id]),
                       rails.now(), MANAGER) if d.invoice_id == row["invoice_id"]), None)
        if (current is None or current.action != row["action"] or current.level != row["level"]
                or current.rule_id != row["rule_id"] or current.due_date != row["due_date"]
                or abs(current.amount_owed - row["amount_owed"]) > 0.005
                or (row["action"] == "reminder" and current.message != row["message"])):
            raise HTTPException(409, "Agreement or payment evidence changed; run a new check before approval.")
    mirror.call("decide_chase", [action_id, "approved" if body.approve else "rejected", by,
                                 _opt(body.note)])
    run_once(rails=rails, mirror=mirror)
    return next(r for r in history(mirror) if r["action_id"] == action_id)


def _daily_loop(interval: float, stop: threading.Event) -> None:
    while not stop.wait(0 if not _last else interval):
        try:
            out = run_once()
            log.info("chaser daily run %s: %s", out["as_of"], out["counts"])
        except Exception as exc:
            _last['error'] = f'{type(exc).__name__}: workflow tick failed; retry scheduled'
            log.exception("chaser daily run failed")


def main() -> None:
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("CHASER_API_PORT", "8773")))


# Preserve a single service module when launched with python -m chaser_agent.api.
if __name__ == '__main__':
    import sys
    sys.modules['chaser_agent.api'] = sys.modules[__name__]

from .workflow_api import router as workflow_router

app.include_router(workflow_router)


if __name__ == "__main__":
    main()
