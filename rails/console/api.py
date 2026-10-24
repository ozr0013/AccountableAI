"""HTTP API for the Judge Console and the live loop:  uvicorn rails.console.api:app --port 8001

Run ONE worker (the workflow runs one event at a time, in this process).
  DEMO_TOKEN=...        require header X-Demo-Token on everything except /health
  CORS_ORIGINS=a,b      browser origins allowed (default: http://localhost:3000); the UI's
                        origin must be listed here or the browser blocks the calls
  LIVE_WARM_ON_START=0  skip the start-up recovery pass (default 1)
  AGENTS / VERIFIER     real | fake, as everywhere (see README)

Facts go in, the system reacts by itself:
  GET  /presets
  POST /presets/{id}    {"params": {}}                  -> record the fact, run the workflow
  POST /text            {"text": "...", "manager_id"}   -> same, from free text
  POST /clock/advance   {"days": 3} | {"to": "2026-04-27"} | {} (next deadline)
  POST /funding-review  {"manager_id": "keyway", "amount": 18400}
What the system did, and the one human touch:
  GET  /feed?limit=100    GET /stats    GET /jobs/{id}
  GET  /approvals         POST /approvals/{id}/decide   {"approver": "controller.dana", "approve": true}

The facts are recorded before the response, so `event_ids` and `affected` come back at once.
The workflow then runs in the background: poll /jobs/{job_id} or watch /feed. Add ?wait=true to
run it before answering (fine with the rules agents, slow with live models).
Reminders are recorded as issued; nothing is emailed. The clock is the demo clock.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ..live.jobs import Jobs
from .console import ConsoleError, ConsoleResult

@asynccontextmanager
async def lifespan(_app):
    """On start, process any fact that was recorded but never handled (a crash mid-run).
    Cheap: events already processed are skipped."""
    if os.getenv("LIVE_WARM_ON_START", "1") == "1":
        try:
            _jobs.submit("startup recovery", live().warm)
        except Exception:      # noqa: BLE001 - the API still starts; /health shows it
            pass
    yield


app = FastAPI(title="AccountableAI live loop", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")],
    allow_methods=["*"], allow_headers=["*"])

_live = None
_jobs = Jobs()


def live():
    global _live
    if _live is None:
        from ..live.system import LiveSystem
        _live = LiveSystem()
    return _live


def console():
    """Kept for older callers: the Judge Console behind the live system."""
    return live().console


def auth(x_demo_token: str | None = Header(default=None)) -> None:
    want = os.getenv("DEMO_TOKEN")
    if want and x_demo_token != want:
        raise HTTPException(401, "missing or wrong X-Demo-Token")


class PresetBody(BaseModel):
    params: dict = Field(default_factory=dict)


class TextBody(BaseModel):
    text: str
    manager_id: str | None = None


class ClockBody(BaseModel):
    days: int | None = None
    to: date | None = None


class FundingBody(BaseModel):
    manager_id: str = "keyway"
    amount: float


class DecideBody(BaseModel):
    approver: str = "controller.dana"
    approve: bool


def _out(r: ConsoleResult) -> dict:
    return {"summary": r.summary, "event_ids": r.event_ids, "affected": r.affected,
            "events": [e.model_dump(mode="json") for e in r.events]}


def _fail(e: Exception, code: int = 422):
    detail = {"error": str(e)}
    if isinstance(e, ConsoleError):
        detail["suggestions"] = e.suggestions
    raise HTTPException(code, detail)


def _run(label: str, fn, events, wait: bool) -> dict:
    """Run the workflow on recorded events: inline (wait) or on the single worker."""
    if wait:
        return {"workflow": fn(events), "job_id": None}
    return {"job_id": _jobs.submit(label, fn, events), "workflow": None}


@app.get("/health")
def health():
    return {"ok": True, "jobs_pending": _jobs.pending()}


@app.get("/presets", dependencies=[Depends(auth)])
def presets():
    return [asdict(p) for p in live().console.presets()]


@app.post("/presets/{preset_id}", dependencies=[Depends(auth)])
def apply_preset(preset_id: str, body: PresetBody, wait: bool = False):
    try:
        rec = live().record_preset(preset_id, body.params)
    except ConsoleError as e:
        _fail(e)
    return {**_out(rec), **_run(f"preset {preset_id}", live().process, rec.events, wait)}


@app.post("/text", dependencies=[Depends(auth)])
def apply_text(body: TextBody, wait: bool = False):
    try:
        rec = live().record_text(body.text, body.manager_id)
    except ConsoleError as e:
        _fail(e)
    return {**_out(rec), **_run("free text", live().process, rec.events, wait)}


@app.post("/clock/advance", dependencies=[Depends(auth)])
def advance_clock(body: ClockBody | None = None, wait: bool = False):
    body = body or ClockBody()
    try:
        scans, target, reason = live().record_clock(body.days, body.to)
    except ValueError as e:
        _fail(e)
    return {"now": target.isoformat(), "reason": reason, "event_ids": [e.id for e in scans],
            **_run("advance clock", live().process, scans, wait)}


@app.post("/funding-review", dependencies=[Depends(auth)])
def funding_review(body: FundingBody, wait: bool = False):
    ev = live().record_funding(body.manager_id, body.amount)
    return {"event_ids": [ev.id], **_run("funding review", live().process, [ev], wait)}


@app.get("/approvals", dependencies=[Depends(auth)])
def approvals():
    return live().pending_approvals()


@app.post("/approvals/{approval_id}/decide", dependencies=[Depends(auth)])
def decide(approval_id: str, body: DecideBody):
    try:
        return live().decide(approval_id, body.approver, body.approve)
    except PermissionError as e:
        raise HTTPException(403, {"error": str(e)}) from e
    except (KeyError, ValueError) as e:
        _fail(e, 404 if isinstance(e, KeyError) else 409)


@app.get("/feed", dependencies=[Depends(auth)])
def feed(limit: int = 100):
    return live().feed(limit)


@app.get("/stats", dependencies=[Depends(auth)])
def stats():
    return {**live().stats(), "jobs_pending": _jobs.pending()}


@app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
def job(job_id: str):
    j = _jobs.get(job_id)
    if j is None:
        raise HTTPException(404, {"error": f"no job {job_id}"})
    return j
