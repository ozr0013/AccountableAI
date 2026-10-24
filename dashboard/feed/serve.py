"""Live feed for the dashboard (NEXT_PUBLIC_FEED=live): the demo session over HTTP.

    python dashboard/feed/serve.py            # http://127.0.0.1:8765, rules-mode agents, free

The Next.js app proxies /api/* here. One in-memory session; POST /api/reset rebuilds it.
Rules-mode agents by default (free). POST /api/live {"on": true} sends the next events to Claude
(Sonnet agents, verifier model stage), metered against FEED_BUDGET dollars (default 1.00, an estimated threshold);
past the cap everything falls back to rules. The briefing uses the free code briefing unless
DASHBOARD_BRIEF_MODEL=1 (one Sonnet call each).
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import threading
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(Path(__file__).parent)]

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from session import DemoSession

app = FastAPI(title="AccountableAI dashboard feed")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
_lock = threading.Lock()
_session: DemoSession | None = None


def session() -> DemoSession:
    global _session
    if _session is None:
        _session = DemoSession(budget=float(os.getenv("FEED_BUDGET", "1.0")))
    return _session


class ConsoleIn(BaseModel):
    preset: str | None = None
    params: dict | None = None
    text: str | None = None


class DecisionIn(BaseModel):
    approve: bool
    by: str = "controller.dana"
    identity_verified: bool = False


class ReplyIn(BaseModel):
    invoice_id: str
    text: str
    proof: Literal["none", "bank", "vendor"] = "none"          # none | bank | vendor


class ClockIn(BaseModel):
    days: int = 1


class LiveIn(BaseModel):
    on: bool


class BriefIn(BaseModel):
    question: str


@app.get("/api/state")
def state():
    with _lock:
        return session().snapshot()


@app.get("/api/presets")
def presets():
    from rails.console.console import PRESETS
    return [{"id": p.id, "label": p.label, "description": p.description, "params": p.params,
             "defaults": p.defaults} for p in PRESETS]


@app.post("/api/console")
def console(body: ConsoleIn):
    with _lock:
        return session().console(body.preset, body.params, body.text)


@app.post("/api/approvals/{approval_id}")
def decide(approval_id: str, body: DecisionIn):
    with _lock:
        return session().decide(approval_id, body.approve, body.by, body.identity_verified)


@app.post("/api/reply")
def reply(body: ReplyIn):
    with _lock:
        return session().reply(body.invoice_id, body.text, body.proof)


@app.post("/api/clock")
def clock(body: ClockIn):
    with _lock:
        return session().advance(body.days)


@app.post("/api/live")
def live_mode(body: LiveIn):
    with _lock:
        r = session().set_live(body.on)
        return {**r, "state": session().snapshot()}


@app.post("/api/brief")
def brief(body: BriefIn):
    with _lock:
        return session().brief(body.question, use_model=os.getenv("DASHBOARD_BRIEF_MODEL") == "1")


@app.get("/api/timeline")
def timeline(days: int = 15):
    """The investigation day by day: facts, agent decisions with reasoning, outcomes, people."""
    with _lock:
        return session().timeline(days)


@app.get("/api/stream")
async def stream(request: Request):
    """Server-Sent Events: each agent decision (reasoning steps, mode, Claude's thinking) and
    each action's start/finish, as they happen. Connect directly to this server (not through
    the Next.js dev proxy, which may buffer the stream)."""
    hub = session().hub
    q = hub.add()

    async def events():
        try:
            yield "retry: 2000\n\n"
            while not await request.is_disconnected():
                try:
                    msg = await asyncio.to_thread(q.get, True, 10)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                yield f"event: {msg['type']}\ndata: {json.dumps(msg, default=str)}\n\n"
        finally:
            hub.remove(q)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/reset")
def reset():
    with _lock:
        session().reset()
        return session().snapshot()


if __name__ == "__main__":
    import uvicorn
    session()                                    # build before the first request
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("FEED_PORT", "8765")), log_level="warning")
