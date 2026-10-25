"""HTTP boundary for the agent pipeline (mounted on the chaser service, port 8773).

    POST /pipeline/dispatch                  queue one chaser task per invoice under an agreement
    POST /pipeline/run                       work the queue: chaser tasks, then anomaly hand-offs
    POST /pipeline/invoices/{id}/run         run the whole chain for one invoice now
    GET  /pipeline/tasks                     the queue (filter by invoice_id, agent, status)
    GET  /pipeline/tasks/{task_id}           one task with its full trace
    GET  /pipeline/flow/{invoice_id}         agreement -> chaser -> anomaly -> controller, in order
"""
from __future__ import annotations

import os
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from . import api as service
from . import pipeline

router = APIRouter(prefix="/pipeline", tags=["Agent pipeline"])
Rails = Annotated[Any, Depends(service.rails_dependency)]


def _needs_claude():
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(400, "the pipeline agents need ANTHROPIC_API_KEY")


def _claude_errors(fn):
    import anthropic
    try:
        return fn()
    except anthropic.RateLimitError as exc:
        raise HTTPException(429, "Claude rate limit; retry shortly") from exc
    except anthropic.APIStatusError as exc:
        raise HTTPException(502, f"Claude API error {exc.status_code}") from exc
    except anthropic.APIConnectionError as exc:
        raise HTTPException(502, "could not reach Claude") from exc


@router.post("/dispatch")
def dispatch(rails: Rails, vendor: str | None = None) -> dict:
    with service._lock, service.workflow_lock(rails):
        queued = pipeline.dispatch(rails, service._mirror(), rails.now(), vendor)
    return {"as_of": rails.now().isoformat(), "queued": queued}


@router.post("/run")
def run(rails: Rails, limit: Annotated[int, Query(ge=1, le=50)] = 10) -> dict:
    _needs_claude()
    with service._lock, service.workflow_lock(rails):
        done = _claude_errors(lambda: pipeline.process(rails, service._mirror(), rails.now(),
                                                       limit=limit))
    return {"as_of": rails.now().isoformat(), "processed": done}


@router.post("/invoices/{invoice_id}/run")
def run_invoice(invoice_id: str, rails: Rails) -> dict:
    _needs_claude()
    mirror, today = service._mirror(), rails.now()
    if not any(l.invoice_id == invoice_id for l in rails.ledger(pipeline.MANAGER)):
        raise HTTPException(404, "unknown invoice")
    with service._lock, service.workflow_lock(rails):
        pipeline.enqueue(mirror, "chaser", invoice_id, "run requested from the dashboard", {},
                         today, "controller")
        done = _claude_errors(lambda: pipeline.process(rails, mirror, today, limit=5,
                                                       invoice_id=invoice_id))
        return {"processed": done, "flow": pipeline.flow(rails, mirror, invoice_id)}


@router.get("/tasks")
def list_tasks(invoice_id: str | None = None,
               agent: Literal["chaser", "anomaly"] | None = None,
               status: Literal["pending", "running", "done", "failed"] | None = None) -> list[dict]:
    rows = pipeline.tasks(service._mirror(), invoice_id, agent, status)
    return [{k: v for k, v in t.items() if k != "trace"} for t in rows]


@router.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    row = next((t for t in pipeline.tasks(service._mirror()) if t["task_id"] == task_id), None)
    if row is None:
        raise HTTPException(404, "unknown task")
    return row


@router.get("/flow/{invoice_id}")
def get_flow(invoice_id: str, rails: Rails) -> dict:
    try:
        return pipeline.flow(rails, service._mirror(), invoice_id)
    except KeyError as exc:
        raise HTTPException(404, "unknown invoice") from exc


# -- time-lapse demo (its own copy of the books; see chaser_agent/demo.py) ------------------------

import threading  # noqa: E402

from . import demo  # noqa: E402

_demo_lock = threading.Lock()


def _demo_rails():
    try:
        r = demo.rails()
        r.now()
        return r
    except Exception as exc:  # noqa: BLE001 - no demo copy yet, or a dropped connection
        demo._forget()
        raise HTTPException(409, "demo not started; POST /pipeline/demo/reset") from exc


@router.get("/stream")
async def stream():
    """Live events (Server-Sent Events): reasoning as it is written, each tool call and result,
    decisions, hand-offs, new evidence and day boundaries."""
    import asyncio
    import json as _json
    import queue as _queue

    from fastapi.responses import StreamingResponse

    from . import runtime
    q = runtime.subscribe()

    async def events():
        try:
            yield "event: ready\ndata: {}\n\n"
            idle = 0.0
            while True:
                try:
                    ev = q.get_nowait()
                except _queue.Empty:
                    await asyncio.sleep(0.05)
                    idle += 0.05
                    if idle >= 15:
                        idle = 0.0
                        yield ": keep-alive\n\n"
                    continue
                idle = 0.0
                yield f"event: {ev['type']}\ndata: {_json.dumps(ev, default=str)}\n\n"
        finally:
            runtime.unsubscribe(q)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/demo")
def demo_state() -> dict:
    return {**demo.state(_demo_rails()), "usage": pipeline.usage_total(service._mirror())}


@router.post("/demo/reset")
def demo_reset(run_first_day: bool = True) -> dict:
    _needs_claude()
    with _demo_lock:
        mirror = service._mirror()
        out = demo.reset(mirror)
        if run_first_day:
            out = _claude_errors(lambda: demo.run_day(demo.rails(), mirror))
        return out


@router.post("/demo/next-day")
def demo_next_day() -> dict:
    _needs_claude()
    with _demo_lock:
        return _claude_errors(lambda: demo.next_day(_demo_rails(), service._mirror()))


@router.get("/demo/invoices")
def demo_invoices() -> list[dict]:
    """Every invoice in the demo books against its deadline (code only, no model calls)."""
    r = _demo_rails()
    return pipeline.ledger_view(r, service._mirror(), r.now())


@router.get("/demo/flow/{invoice_id}")
def demo_flow(invoice_id: str) -> dict:
    try:
        return pipeline.flow(_demo_rails(), service._mirror(), invoice_id)
    except KeyError as exc:
        raise HTTPException(404, "unknown invoice") from exc


@router.get("/demo/activity")
def demo_activity(limit: Annotated[int, Query(ge=1, le=500)] = 200) -> list[dict]:
    """Everything the agents and people did, newest first: tasks, approvals, deliveries."""
    from . import connected
    mirror, rails = service._mirror(), _demo_rails()
    items = []
    for t in pipeline.tasks(mirror):
        o = t["outcome"]
        items.append({"on": t["as_of"], "who": t["agent"], "invoice_id": t["invoice_id"],
                      "kind": "task", "task_id": t["task_id"], "status": t["status"],
                      "decision": o.get("decision"), "verdict": o.get("verdict"),
                      "by": o.get("decided_by") or ("claude" if o.get("decision") else None),
                      "text": o.get("summary") or o.get("rationale") or o.get("error") or t["reason"],
                      "created_by": t["created_by"]})
    for h in service.history(mirror):
        if h.get("decided_by") in ("controller",):
            items.append({"on": h["as_of"], "who": "controller", "invoice_id": h["invoice_id"],
                          "kind": "approval", "status": h["status"],
                          "text": f"{h['status']} the {h.get('level') or h['action']} "
                                  f"({h.get('decision_note') or 'no note'})"})
    for c in connected.cases(rails, pipeline.MANAGER):
        for o in c["outbox"]:
            if o["status"] == "delivered":
                items.append({"on": o.get("delivered_on", o["created_on"]), "who": "outbox",
                              "invoice_id": o["invoice_id"], "kind": "delivery",
                              "status": "delivered", "text": f"{o['kind']} delivered to {o['audience']}"})
    items.sort(key=lambda i: (i["on"], i["kind"] != "task"), reverse=True)
    return items[:limit]


@router.get("/demo/agreements")
def demo_agreements() -> list[dict]:
    """Agreements the agreement agent saved, with the fields it found and the steps it took."""
    import json as _json
    out = []
    for a in service._mirror().query("SELECT * FROM agreement"):
        out.append({k: a.get(k) for k in ("doc_id", "filename", "vendor_name", "status", "complete",
                                          "missing", "payment_due", "payment_amount",
                                          "payment_info", "mode", "cost_usd", "confirmed_by")}
                   | {"fields": _json.loads(a.get("fields") or "[]"),
                      "steps": _json.loads(a.get("steps") or "[]")})
    return out


@router.post("/demo/actions/{action_id}/decide")
def demo_decide(action_id: str, approve: bool, note: str = "") -> dict:
    """The controller approves or rejects a firm or escalation reminder the chaser drafted."""
    mirror = service._mirror()
    row = next((h for h in service.history(mirror) if h["action_id"] == action_id), None)
    if row is None:
        raise HTTPException(404, "unknown chaser action")
    if row["status"] not in ("needs_approval", "needs_controller"):
        raise HTTPException(409, f"action is {row['status']}; nothing to decide")
    mirror.call("decide_chase", [action_id, "approved" if approve else "rejected", "controller",
                                 {"some": note or ("approved" if approve else "rejected")}])
    return next(h for h in service.history(mirror) if h["action_id"] == action_id)
