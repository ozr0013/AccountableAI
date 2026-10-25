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
        _session = DemoSession(budget=float(os.getenv("FEED_BUDGET", "1.0")), send_email=True)
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


@app.post("/api/loop/{step}")
def loop_step(step: Literal["start", "agreement", "chaser", "anomaly"]):
    """The three-agent loop, one agent per call (dashboard Live loop page). start = fresh session."""
    import loop
    with _lock:
        s = session()
        if step == "start":
            s.reset()
            return {"ok": True, "as_of": s.rails.now().isoformat(), "live": s.live}
        out = {"agreement": loop.agreement_step, "chaser": loop.chaser_step,
               "anomaly": loop.anomaly_step}[step](s)
        return {"ok": True, **out, "as_of": s.rails.now().isoformat()}


@app.get("/api/timeline")
def timeline(days: int = 15):
    """The investigation day by day: facts, agent decisions with reasoning, outcomes, people."""
    with _lock:
        return session().timeline(days)


@app.get("/api/stream")
async def stream(request: Request):
    """Server-Sent Events, as they happen: `step` (a workflow step finished), `agent_started`
    (which agent runs next, on which invoice), `thinking` (a piece of Claude's summarized
    thinking, streamed), `decision` (the finished record: reasoning steps, mode, citations),
    and `action` (an action's start/finish). Connect directly to this server (not through
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


# -- the UI revamp 2 frontend (ui-revamp-2/), served by this backend --------------------------
_ADDR = None
_ALERT_ISSUE = {
    "unexplained_unpaid": "No proof the vendor was paid", "duplicate_billing": "Possible duplicate invoice",
    "bank_detail_change": "Bank-detail change request", "paperwork_request": "Vendor paperwork missing (W-9)",
    "owner_pending_approval": "Oakline approval pending", "statement_mismatch": "Statement amount differs from approved",
}


@app.get("/api/ui/invoices")
def ui_invoices():
    """Every Keyway invoice in the shape ui-revamp-2/app.js uses: property (the address on the
    vendor's invoice), vendor, amount, status Paid | Outstanding | Past Due, due date, and the
    issue a person should review (an open alert other than the lateness pattern)."""
    import re
    global _ADDR
    _ADDR = _ADDR or re.compile(r"\b\d{1,5} [A-Z][a-z]+(?: [A-Z][a-z]+)? (?:St|Ln|Ct|Dr|Ave|Rd|Way|Pl|Blvd)\b")
    with _lock:
        s = session()
        snap = s.snapshot()
        texts = {d.data.get("invoice_id"): d.text for d in s.rails.documents("keyway")
                 if d.kind.value == "vendor_invoice" and d.data.get("invoice_id")}
    out = []
    for it in snap["items"]:
        text = texts.get(it["id"], "")
        where = _ADDR.search(text)
        desc = re.sub(r"^.*?Invoice [A-Z0-9-]+\.\s*", "", text).split(" Amount due")[0].strip(" .")
        kinds = [a.split("-")[1] for a in it.get("alerts") or [] if a.count("-") >= 2]
        issues = [_ALERT_ISSUE[k] for k in kinds if k in _ALERT_ISSUE]
        if it.get("owed", 0) <= 0.005 and it.get("status") != "unfunded":
            status = "Paid"
        elif it.get("days_late", 0) > 0 or it.get("state") == "delayed":
            status = "Past Due"
        else:
            status = "Outstanding"
        out.append({"id": it["id"], "cid": it.get("vendor_id") or "", "vendor": it["vendor"],
                    "property": where.group(0) if where else "Portfolio-wide",
                    "description": desc or (it.get("trade") or "").title(), "amount": it["amount"],
                    "status": status, "due": it.get("due") or it.get("received") or snap["meta"]["as_of"],
                    "issue": "; ".join(issues), "cause": it.get("cause_text") or "", "reviewed": False,
                    "notes": []})
    props = sorted({i["property"] for i in out if i["property"] != "Portfolio-wide"})
    if any(i["property"] == "Portfolio-wide" for i in out):
        props.append("Portfolio-wide")
    dues = sorted(i["due"] for i in out)
    return {"as_of": snap["meta"]["as_of"], "manager": snap["meta"]["manager"], "properties": props,
            "start": dues[0] if dues else snap["meta"]["as_of"], "end": dues[-1] if dues else snap["meta"]["as_of"],
            "invoices": out, "agents": snap["meta"]["agents"]}


# Last, so every /api route above wins: the frontend at this server's root.
_UI = ROOT / "ui-revamp-2"
if _UI.is_dir():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(_UI), html=True), name="ui")


if __name__ == "__main__":
    import uvicorn
    session()                                    # build before the first request
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("FEED_PORT", "8765")), log_level="warning")
