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
            created_by: str, key: str | None = None) -> str:
    """`key`: a second task for the same invoice on the same day (e.g. woken by the console)."""
    task_id = f"{agent}:{invoice_id}:{today.isoformat()}" + (f":{key}" if key else "")
    mirror.call("enqueue_task", [task_id, agent, invoice_id, created_by, reason,
                                 json.dumps(payload, default=str), today.isoformat()])
    return task_id


def dispatch(rails, mirror, today: date, vendor: str | None = None,
             created_by: str = "daily_schedule", since: date | None = None) -> list[str]:
    """One chaser task per invoice whose vendor has saved agreement rules (no model calls).
    `since`: skip invoices already paid in full before that day (settled history)."""
    from workflow.names import names_from
    name = names_from(rails)
    by_vendor = daily._rules_by_vendor(mirror.query("SELECT * FROM agreement_rule"))
    manager = name(MANAGER)
    reviewed = {t["invoice_id"] for t in tasks(mirror)
                if t["agent"] == "anomaly" or t["outcome"].get("decision") == "verified_paid"}
    bank = daily.bank_rows_by_invoice(rails, MANAGER, today)
    out = []
    for ledger_line in rails.ledger(MANAGER):
        v = name(ledger_line.vendor_id)
        line, _ = daily.verified_line(ledger_line, bank.get(ledger_line.invoice_id, []))
        settled = line.paid_amount >= line.amount
        # Reviewed and paid in full: done. Reviewed but a balance is still owed: keep chasing it.
        if line.duplicate_of or not daily.rules_for(by_vendor, v, manager) or (
                line.invoice_id in reviewed and settled):
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


def model_waits(mirror, invoice_id: str, today: date) -> int:
    """Days in a row, up to yesterday, on which Claude chose to wait although it could act."""
    n = 0
    for t in reversed(tasks(mirror, invoice_id=invoice_id, agent="chaser")):
        if t["as_of"] >= today.isoformat() or t["status"] != "done":
            continue
        o = t["outcome"]
        if o.get("decision") != "wait" or o.get("decided_by"):
            break               # it acted, or the schedule (not Claude) waited
        n += 1
    return n


def run_chaser(task, rails, mirror, today, client=None) -> dict:
    from . import api as service
    from . import llm_chaser
    history_rows = service.delivery_history(rails, service.history(mirror))

    def handoff(agent, invoice_id, reason, payload):
        return enqueue(mirror, agent, invoice_id, reason, payload, today, "chaser_agent")

    tools = llm_chaser.ChaserTools(rails, mirror, task["invoice_id"], today, history_rows,
                                   handoff, _record_chase(mirror, today), MANAGER,
                                   model_waits=model_waits(mirror, task["invoice_id"], today))
    if tools.allowed() == ["wait"]:
        # Nothing to decide today (not due, waiting on approval or delivery, no agreement):
        # the schedule waits without a model call, and says why.
        reason = tools.code.reason if tools.code else "no ledger line for this invoice"
        return {"outcome": {"decision": "wait", "rationale": reason, "decided_by": "schedule",
                            "result": {"waiting": True}, "error": None},
                "trace": {"agent": "chaser", "model": "schedule (no model call)", "thinking": [],
                          "steps": [], "decision": {"tool": "wait", "input": {"rationale": reason}},
                          "text": "", "usage": {}, "error": None}}
    if tools.allowed() == ["handoff_to_anomaly"]:
        # A payment the code can fully verify (whole amount, the invoice's vendor, by the
        # deadline, nothing else odd) needs no model: it is closed here, without an anomaly task.
        from . import connected
        case = next((c for c in connected.cases(rails, MANAGER)
                     if c["invoice_id"] == task["invoice_id"]), None)
        line = tools.line
        if (case is not None and not case["findings"] and line is not None
                and line.paid_amount >= line.amount and tools.code.due_date
                and line.paid_on and line.paid_on.isoformat() <= tools.code.due_date):
            reason = (f"bank shows ${line.paid_amount:,.2f} paid on {line.paid_on} to the invoice's vendor, "
                      f"by the {tools.code.due_date} deadline; verified by code")
            return {"outcome": {"decision": "verified_paid", "rationale": reason, "decided_by": "code",
                                "result": {"verified": True}, "error": None},
                    "trace": {"agent": "chaser", "model": "code check (no model call)", "thinking": [],
                              "steps": [], "decision": {"tool": "verified_paid", "input": {"rationale": reason}},
                              "text": "", "usage": {}, "error": None}}
    note = task["reason"] if task.get("created_by") in ("investigator", "console") else None
    trace = llm_chaser.run(tools, client, note)
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


def run_investigator(task, rails, mirror, today, client=None) -> dict:
    from . import investigator

    def handoff(agent, invoice_id, reason, payload, day, created_by):
        return enqueue(mirror, agent, invoice_id, reason, payload, day, created_by)
    tools = investigator.InvestigatorTools(rails, mirror, today, _record_chase(mirror, today), handoff, MANAGER)
    lead = task["reason"] if task.get("created_by") not in (None, "daily_schedule") else None
    trace = investigator.run(tools, client, lead)
    return {"outcome": investigator.outcome(trace), "trace": trace.to_dict()}


RUNNERS = {"chaser": run_chaser, "anomaly": run_anomaly, "investigator": run_investigator}
ORDER = {"investigator": 0, "chaser": 1, "anomaly": 2}       # the sweep's notes reach the chaser


def process(rails, mirror, today: date, client=None, limit: int = 20,
            invoice_id: str | None = None) -> list[dict]:
    """Work pending tasks (chasers first; hand-offs created on the way are picked up too)."""
    done = []
    for _ in range(limit):
        pending = [t for t in tasks(mirror, invoice_id=invoice_id, status="pending")
                   if t["as_of"] <= today.isoformat()]
        if not pending:
            break
        task = sorted(pending, key=lambda t: (ORDER.get(t["agent"], 9), t["task_id"]))[0]
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
    rules = daily.rules_for(daily._rules_by_vendor(mirror.query("SELECT * FROM agreement_rule")),
                            vendor, names_from(rails)(MANAGER))
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
    return str(daily.funded_deadline(
        daily.add_days(line.invoice_received, int(params["days"]), params.get("basis", "calendar")), line))


def _statement_claims(rails, invoice_id: str) -> list[dict]:
    """What the middleman's statements say about this invoice (a claim, not proof), oldest first."""
    out = [{"statement": d.id, "dated": str(d.dated), "says": row.get("status"),
            "amount": row.get("amount"), "paid_on": row.get("on")}
           for d in rails.documents(MANAGER) if d.kind.value == "manager_statement"
           for row in d.data.get("lines", []) if row.get("invoice_id") == invoice_id]
    return sorted(out, key=lambda c: c["dated"])


def usage_by_invoice(all_tasks) -> dict[str, dict]:
    """Model usage per task subject: an invoice id, or "portfolio" for the investigator's sweep."""
    usage: dict[str, dict] = {}
    for t in all_tasks:
        u = (t.get("trace") or {}).get("usage") or {}
        if u.get("output_tokens"):
            acc = usage.setdefault(t["invoice_id"], {"calls": 0, "input": 0, "cached": 0, "output": 0})
            acc["calls"] += 1
            acc["input"] += (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
            acc["cached"] += u.get("cache_read_input_tokens") or 0
            acc["output"] += u.get("output_tokens") or 0
    return usage


def usage_total(mirror) -> dict:
    """Every agent's model usage, the portfolio sweep included (the per-invoice rows miss it)."""
    total = {"calls": 0, "input": 0, "cached": 0, "output": 0}
    for acc in usage_by_invoice(tasks(mirror)).values():
        for k in total:
            total[k] += acc[k]
    return total


def ledger_view(rails, mirror, today: date) -> list[dict]:
    """Every invoice checked against its deadline, by code (no model): what was funded, when it
    was due and under which agreement, whether and when it was paid, how many reminders went out,
    and what the agents last decided. The live agents work only the rows that need a decision."""
    from workflow.names import names_from

    from . import api as service
    name = names_from(rails)
    manager = name(MANAGER)
    by_vendor = daily._rules_by_vendor(mirror.query("SELECT * FROM agreement_rule"))
    actions: dict[str, list[dict]] = {}
    for h in service.history(mirror):
        actions.setdefault(h["invoice_id"], []).append(h)
    last_task: dict[str, dict] = {}
    all_tasks = tasks(mirror)
    usage = usage_by_invoice(all_tasks)
    for t in all_tasks:
        if t["status"] in ("done", "failed"):
            last_task[t["invoice_id"]] = t
    out = []
    bank = daily.bank_rows_by_invoice(rails, MANAGER, today)
    for ledger_line in rails.ledger(MANAGER):
        if ledger_line.duplicate_of:
            continue
        rows_for = bank.get(ledger_line.invoice_id, [])
        line, _ = daily.verified_line(ledger_line, rows_for)
        vendor = name(line.vendor_id)
        own = by_vendor.get(daily.norm_name(vendor))
        rules = daily.rules_for(by_vendor, vendor, manager)
        term = next((r for r in rules if r["rule_type"] == "pay_within_days" and r["status"] == "active"), None)
        due = (daily.funded_deadline(daily.add_days(line.invoice_received, int(term["params"]["days"]),
                                                    term["params"].get("basis", "calendar")), line)
               if term and line.invoice_received else None)
        paid_on = line.paid_on if line.paid_on and line.paid_on <= today else None
        paid = line.paid_amount if paid_on else 0.0
        misdirected = bool(rows_for) and line.paid_amount <= 0 and any(
            r.get("on", "") <= today.isoformat() for r in rows_for)
        if misdirected:
            state, days_ = "misdirected", None
        elif not line.funded_amount:
            recent = line.invoice_received and (today - line.invoice_received).days <= 3
            state, days_ = ("awaiting_funding" if recent else "not_funded"), None
        elif paid and paid + 0.005 < line.amount:
            state, days_ = "partial", None
        elif paid:
            days_ = (paid_on - due).days if due else None
            state = "paid_late" if days_ and days_ > 0 else "paid_on_time"
        elif due is None:
            state, days_ = "no_deadline", None
        elif today > due:
            state, days_ = "overdue", (today - due).days
        else:
            state, days_ = "not_due", (due - today).days
        rows = actions.get(line.invoice_id, [])
        reminders = [h for h in rows if h["action"] == "reminder" and h["status"] in ("queued", "approved")]
        t = last_task.get(line.invoice_id)
        out.append({
            "invoice_id": line.invoice_id, "vendor": vendor, "amount": line.amount,
            "funded_amount": line.funded_amount, "deadline": due.isoformat() if due else None,
            "deadline_source": ("vendor agreement" if own else "management agreement") if term else None,
            "paid_on": paid_on.isoformat() if paid_on else None, "paid_amount": paid,
            "state": state, "days": days_,
            "reminders": sorted({h["level"] for h in reminders if h.get("level")},
                                key=lambda lv: daily.LEVEL_ORDER.index(lv)),
            "escalated": any(h["action"] == "escalate" for h in rows),
            "flags": [h["level"] for h in rows if h["action"] == "flag"],
            "usage": usage.get(line.invoice_id),
            "last": ({"agent": t["agent"], "day": t["as_of"], "decision": t["outcome"].get("decision"),
                      "verdict": t["outcome"].get("verdict"),
                      "by": t["outcome"].get("decided_by") or "claude"}
                     if t else None),
        })
    return out
