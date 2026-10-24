"""Live feed for the dashboard (NEXT_PUBLIC_FEED=live): the demo session over HTTP.

    python dashboard/feed/serve.py            # http://127.0.0.1:8765, rules-mode agents, free

The Next.js app proxies /api/* here. One in-memory session; POST /api/reset rebuilds it.
The briefing uses the free code briefing unless DASHBOARD_BRIEF_MODEL=1 (one Sonnet call each).
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(Path(__file__).parent)]

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from session import DemoSession

app = FastAPI(title="AccountableAI dashboard feed")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
_lock = threading.Lock()
_session: DemoSession | None = None


def session() -> DemoSession:
    global _session
    if _session is None:
        _session = DemoSession()
    return _session


class ConsoleIn(BaseModel):
    preset: str | None = None
    params: dict | None = None
    text: str | None = None


class DecisionIn(BaseModel):
    approve: bool
    by: str = "controller.dana"


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
        return session().decide(approval_id, body.approve, body.by)


@app.post("/api/brief")
def brief(body: BriefIn):
    with _lock:
        return session().brief(body.question, use_model=os.getenv("DASHBOARD_BRIEF_MODEL") == "1")


@app.post("/api/reset")
def reset():
    with _lock:
        session().reset()
        return session().snapshot()


if __name__ == "__main__":
    import uvicorn
    session()                                    # build before the first request
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("FEED_PORT", "8765")), log_level="warning")
