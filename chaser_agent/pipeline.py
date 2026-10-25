"""End-to-end agent pipeline over a SpacetimeDB task queue.

    agreement agent  --saves rules-->  dispatch(): one chaser task per invoice under an agreement
    chaser agent     --handoff_to_anomaly-->  anomaly task
    anomaly agent    --record_verdict-->  hand-off reviewed, case to the controller

Each agent takes a pending task, marks it running, runs Claude with its tools and skill, and
stores the outcome and full trace on the task. Hand-offs are new tasks, so every step survives
restarts and the frontend can show the chain for any invoice.
"""
from __future__ import annotations

import json
from datetime import date

from . import daily

MANAGER = "keyway"


def _opt(v):
    return {"none": []} if v is None else {"some": v}


def tasks(mirror, invoice_id=None, agent=None, status=None) -> list[dict]:
    rows = mirror.query("SELECT * FROM agent_task")
    out = []
    for r in rows:
        if invoice_id not in (None, r["invoice_id"]) or agent not in (None, r["agent"]) \
                or status not in (None, r["status"]):
            continue
        out.append({**r, "payload": json.loads(r["payload"] or "{}"),
                    "outcome": json.loads(r["outcome"] or "{}"),
                    "trace": json.loads(r["trace"] or "{}")})
    return sorted(out, key=lambda t: (t["as_of"], str(t["created_at"]), t["task_id"]))


def enqueue(mirror, agent: str, invoice_id: str, reason: str, payload: dict, today: date,
            created_by: str) -> str:
    task_id = f"{agent}:{invoice_id}:{today.isoformat()}"
    mirror.call("enqueue_task", [task_id, agent, invoice_id, created_by, reason,
                                 json.dumps(payload, default=str), today.isoformat()])
    return task_id


def dispatch(rails, mirror, today: date, vendor: str | None = None,
             created_by: str = "daily_schedule", since: date | None = None) -> list[str]:
    """One chaser task per invoice whose vendor has saved agreement rules (no model calls).
    `since`: skip invoices already paid in full before that day (settled history)."""
    from workflow.names import names_from
    name = names_from(rails)
    covered = {daily.norm_name(r["vendor_name"]) for r in mirror.query("SELECT * FROM agreement_rule")}
    done = {t["invoice_id"] for t in tasks(mirror, agent="anomaly")}
    out = []
    for line in rails.ledger(MANAGER):
        v = name(line.vendor_id)
        if line.duplicate_of or daily.norm_name(v) not in covered or line.invoice_id in done:
            continue
        if vendor and daily.norm_name(vendor) != daily.norm_name(v):
            continue
        if since and line.paid_on and line.paid_on < since and line.paid_amount >= line.amount:
            continue
        out.append(enqueue(mirror, "chaser", line.invoice_id,
                           f"daily check for {v}", {"vendor": v}, today, created_by))
    return out


def _set(mirror, task_id, status, outcome=None, trace=None):
    mirror.call("update_task", [task_id, status, json.dumps(outcome or {}, default=str),
                                json.dumps(trace or {}, default=str)])


def _record_chase(mirror, today):
    def record(d, reason):
        mirror.call("record_chase", [
            d.action_id(), d.invoice_id, d.vendor_name, d.action, _opt(d.level),
            _opt(d.due_date), d.days_overdue, float(d.amount_owed), _opt(d.message), reason,
            _opt(d.rule_id), _opt(d.rule_quote), d.status, today.isoformat()])
    return record


def run_chaser(task, rails, mirror, today, client=None) -> dict:
    from . import api as service
    from . import llm_chaser
    history_rows = service.delivery_history(rails, service.history(mirror))

    def handoff(agent, invoice_id, reason, payload):
        return enqueue(mirror, agent, invoice_id, reason, payload, today, "chaser_agent")

    tools = llm_chaser.ChaserTools(rails, mirror, task["invoice_id"], today, history_rows,
                                   handoff, _record_chase(mirror, today), MANAGER)
    if tools.allowed() == ["wait"]:
        # Nothing to decide today (not due, waiting on approval or delivery, no agreement):
        # the schedule waits without a model call, and says why.
        reason = tools.code.reason if tools.code else "no ledger line for this invoice"
        return {"outcome": {"decision": "wait", "rationale": reason, "decided_by": "schedule",
                            "result": {"waiting": True}, "error": None},
                "trace": {"agent": "chaser", "model": "schedule (no model call)", "thinking": [],
                          "steps": [], "decision": {"tool": "wait", "input": {"rationale": reason}},
                          "text": "", "usage": {}, "error": None}}
    trace = llm_chaser.run(tools, client)
    return {"outcome": llm_chaser.outcome(trace), "trace": trace.to_dict()}


def run_anomaly(task, rails, mirror, today, client=None) -> dict:
    from anomaly_agent import agent as anomaly

    from . import api as service
    from . import connected
    cases = connected.reconcile(rails, mirror, MANAGER, as_of=today)
    case = next((c for c in cases if c["invoice_id"] == task["invoice_id"]), None)
    line = next((l for l in rails.ledger(MANAGER) if l.invoice_id == task["invoice_id"]), None)
    documents = connected._case_documents(rails, line, today) if line else []
    handoff_id = task["payload"].get("handoff_action_id")

    def close_handoff(verdict, summary):
        row = next((h for h in service.history(mirror) if h["action_id"] == handoff_id), None)
        if row and row["status"] == "recorded":
            mirror.call("decide_chase", [handoff_id, "reviewed", "anomaly_agent",
                                         _opt(f"{verdict}: {summary}"[:1000])])

    tools = anomaly.AnomalyTools(rails, mirror, task["invoice_id"], case, documents, today,
                                 close_handoff, MANAGER)
    trace = anomaly.run(tools, task["reason"], client)
    return {"outcome": anomaly.outcome(trace), "trace": trace.to_dict()}


RUNNERS = {"chaser": run_chaser, "anomaly": run_anomaly}


def process(rails, mirror, today: date, client=None, limit: int = 20,
            invoice_id: str | None = None) -> list[dict]:
    """Work pending tasks (chasers first; hand-offs created on the way are picked up too)."""
    done = []
    for _ in range(limit):
        pending = [t for t in tasks(mirror, invoice_id=invoice_id, status="pending")
                   if t["as_of"] <= today.isoformat()]
        if not pending:
            break
        task = sorted(pending, key=lambda t: (t["agent"] != "chaser", t["task_id"]))[0]
        _set(mirror, task["task_id"], "running")
        from . import runtime
        token = runtime.CONTEXT.set({"task_id": task["task_id"], "agent": task["agent"],
                                     "invoice_id": task["invoice_id"], "day": today.isoformat()})
        runtime.emit({"type": "task_started", "reason": task["reason"],
                      "created_by": task["created_by"]})
        try:
            result = RUNNERS[task["agent"]](task, rails, mirror, today, client)
            status = "failed" if result["outcome"].get("error") else "done"
            _set(mirror, task["task_id"], status, result["outcome"], result["trace"])
        except Exception as exc:  # noqa: BLE001 - a failed task is recorded, the queue goes on
            result = {"outcome": {"error": f"{type(exc).__name__}: {exc}"[:500]}, "trace": {}}
            _set(mirror, task["task_id"], "failed", result["outcome"])
            status = "failed"
        runtime.emit({"type": "task_done", "status": status, "outcome": result["outcome"]})
        runtime.CONTEXT.reset(token)
        done.append({"task_id": task["task_id"], "agent": task["agent"],
                     "invoice_id": task["invoice_id"], **result["outcome"]})
    return done


def flow(rails, mirror, invoice_id: str) -> dict:
    """The whole chain for one invoice, in order: agreement -> chaser -> anomaly -> controller."""
    from workflow.names import names_from

    from . import api as service
    from . import connected
    line = next((l for l in rails.ledger(MANAGER) if l.invoice_id == invoice_id), None)
    if line is None:
        raise KeyError(invoice_id)
    vendor = names_from(rails)(line.vendor_id)
    key = daily.norm_name(vendor)
    agreements = [a for a in mirror.query("SELECT * FROM agreement")
                  if daily.norm_name(a.get("vendor_name") or "") == key]
    rules = [r for r in mirror.query("SELECT * FROM agreement_rule")
             if daily.norm_name(r["vendor_name"]) == key]
    case = next((c for c in connected.cases(rails, MANAGER) if c["invoice_id"] == invoice_id), None)
    return {
        "invoice": line.model_dump(mode="json"), "vendor": vendor,
        "agreement": [{k: a.get(k) for k in ("doc_id", "filename", "status", "payment_due",
                                             "payment_amount", "confirmed_by")} for a in agreements],
        "rules": [{k: r.get(k) for k in ("rule_id", "rule_type", "status", "check")} for r in rules],
        "tasks": tasks(mirror, invoice_id=invoice_id),
        "chaser_actions": [h for h in service.history(mirror) if h["invoice_id"] == invoice_id],
        "case": case,
        "deadline": _deadline(line, rules),
        "statements": _statement_claims(rails, invoice_id),
    }


def _deadline(line, rules: list[dict]) -> str | None:
    """The agreement's payment deadline for this invoice: receipt + pay_within_days."""
    term = next((r for r in rules if r["rule_type"] == "pay_within_days" and r["status"] == "active"), None)
    if term is None or line.invoice_received is None:
        return None
    params = json.loads(term["params"]) if isinstance(term["params"], str) else term["params"]
    return str(daily.add_days(line.invoice_received, int(params["days"]), params.get("basis", "calendar")))


def _statement_claims(rails, invoice_id: str) -> list[dict]:
    """What the middleman's statements say about this invoice (a claim, not proof), oldest first."""
    out = [{"statement": d.id, "dated": str(d.dated), "says": row.get("status"),
            "amount": row.get("amount"), "paid_on": row.get("on")}
           for d in rails.documents(MANAGER) if d.kind.value == "manager_statement"
           for row in d.data.get("lines", []) if row.get("invoice_id") == invoice_id]
    return sorted(out, key=lambda c: c["dated"])
