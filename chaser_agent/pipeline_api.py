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
