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
import threading
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import daily

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
log = logging.getLogger(__name__)
MANAGER = "keyway"

_lock = threading.Lock()
_last: dict = {}


@asynccontextmanager
async def _lifespan(_app):
    stop = threading.Event()
    if os.getenv("CHASER_DAILY", "off").lower() == "on":
        interval = float(os.getenv("CHASER_INTERVAL_S", "86400"))
        threading.Thread(target=_daily_loop, args=(interval, stop), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="Chaser agent", description=__doc__ or "", lifespan=_lifespan)


def _opt(v):
    return {"none": []} if v is None else {"some": v}


def _mirror():
    from rails.live.spacetime import SpacetimeMirror
    return SpacetimeMirror()


def _rails():
    from rails.real import RealRails
    return RealRails()


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
    with _lock:
        rails, mirror = rails or _rails(), mirror or _mirror()
        today = as_of or rails.now()
        decisions = daily.run(rails, mirror.query("SELECT * FROM agreement_rule"),
                              history(mirror), today, MANAGER)
        saved = 0
        for d in decisions:
            if not d.recorded:
                continue
            mirror.call("record_chase", [
                d.action_id(), d.invoice_id, d.vendor_name, d.action, _opt(d.level),
                _opt(d.due_date), d.days_overdue, float(d.amount_owed), _opt(d.message), d.reason,
                _opt(d.rule_id), _opt(d.rule_quote), d.status, today.isoformat()])
            saved += 1
        counts: dict[str, int] = {}
        for d in decisions:
            counts[d.action] = counts.get(d.action, 0) + 1
        _last.update(as_of=today.isoformat(), counts=counts, saved=saved)
        return {"as_of": today.isoformat(), "counts": counts, "saved": saved,
                "decisions": [d.to_dict() for d in decisions if d.action != "skip"]}


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model_available": bool(os.getenv("ANTHROPIC_API_KEY")),
            "daily": os.getenv("CHASER_DAILY", "off"), "last_run": _last or None}


@app.post("/chaser/run")
def run(as_of: date | None = Query(None, description="check as of this day (default: world today)")):
    try:
        return run_once(as_of)
    except OSError as exc:
        raise HTTPException(502, f"database unavailable: {exc}") from exc


@app.get("/chaser/actions")
def actions(invoice_id: str | None = None) -> list[dict]:
    rows = history(_mirror())
    return sorted((r for r in rows if invoice_id in (None, r["invoice_id"])),
                  key=lambda r: (r["as_of"], r["invoice_id"]))


@app.get("/chaser/patterns")
def patterns(vendor: str | None = None) -> list[dict]:
    from .patterns import vendor_patterns
    rails = _rails()
    rows = vendor_patterns(rails, _mirror().query("SELECT * FROM agreement_rule"), rails.now(),
                           MANAGER)
    return [r for r in rows if not vendor or vendor.lower() in r["vendor"].lower()]


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=2000)


@app.post("/chaser/ask")
def ask(body: AskIn) -> dict:
    """Claude picks read-only tools to answer; the response lists every tool it called."""
    import anthropic

    from .assistant import DataTools
    from .assistant import ask as run_ask
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(400, "needs ANTHROPIC_API_KEY")
    rails, mirror = _rails(), _mirror()
    tools = DataTools(rails, mirror, lambda: history(mirror), rails.now(), MANAGER)
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
def reply(body: ReplyIn) -> dict:
    """Claude reads the manager's reply; only a bank record or vendor confirmation is proof."""
    from agents.chaser import ChaserAgent
    from agents.llm import default_llm
    rails = _rails()
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
def decide(action_id: str, body: DecideIn, by: str = Depends(_controller)) -> dict:
    mirror = _mirror()
    row = next((r for r in history(mirror) if r["action_id"] == action_id), None)
    if row is None:
        raise HTTPException(404, "unknown chaser action")
    if row["status"] not in ("needs_approval", "needs_controller"):
        raise HTTPException(409, f"action is {row['status']}; nothing to decide")
    mirror.call("decide_chase", [action_id, "approved" if body.approve else "rejected", by,
                                 _opt(body.note)])
    return next(r for r in history(mirror) if r["action_id"] == action_id)


def _daily_loop(interval: float, stop: threading.Event) -> None:
    while not stop.wait(0 if not _last else interval):
        try:
            out = run_once()
            log.info("chaser daily run %s: %s", out["as_of"], out["counts"])
        except Exception:
            log.exception("chaser daily run failed")


def main() -> None:
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("CHASER_API_PORT", "8773")))


if __name__ == "__main__":
    main()
