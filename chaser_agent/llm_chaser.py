"""The chaser as a Claude agent: one task = one invoice, Claude chooses the next step.

Claude reads the invoice context with a tool, then makes exactly one decision with a decision
tool. The decision tools are guardrails: they accept only what the approved schedule allows today
(the deterministic check in daily.py, exposed to Claude as `policy`), so Claude can choose to wait
or explain, but cannot skip a reminder level, escalate early or hand off without bank evidence.
A hand-off creates a task for the anomaly agent.
"""
from __future__ import annotations

from datetime import date

from . import daily
from .patterns import describe, vendor_patterns
from .runtime import Tool, Trace, load_skill, run_agent

SYSTEM = """You are the chaser agent for Oakline Homes, a property owner. Oakline funds a \
management company (Keyway), which must pay each vendor by the deadline in that vendor's \
agreement. You handle one invoice per task: read its context, then make exactly one decision \
using a decision tool. Follow the chasing skill. Base every statement on tool results; cite \
rule ids, dates and amounts. You never write or send messages yourself: reminders are \
controller-approved templates, and firm or escalation steps still go to the controller."""

RATIONALE = {"type": "string", "description": "1-3 neutral sentences: deadline, how overdue, "
             "what was already sent, why this step; cite the rule id", "minLength": 10,
             "maxLength": 1200}


def _schema(**props):
    return {"type": "object", "additionalProperties": False, "required": list(props),
            "properties": props}


class ChaserTools:
    def __init__(self, rails, mirror, invoice_id: str, today: date, history_rows: list[dict],
                 enqueue, record, manager_id: str = "keyway"):
        from workflow.names import names_from
        self.rails, self.mirror, self.invoice_id, self.today = rails, mirror, invoice_id, today
        self.history_rows, self.enqueue, self.record = history_rows, enqueue, record
        self.manager_id, self.name = manager_id, names_from(rails)
        self.line = next((l for l in rails.ledger(manager_id) if l.invoice_id == invoice_id), None)
        self.rule_rows = mirror.query("SELECT * FROM agreement_rule")
        vendor = self.name(self.line.vendor_id) if self.line else ""
        self.vendor = vendor
        self.rules = daily.rules_for(daily._rules_by_vendor(self.rule_rows), vendor, self.name(manager_id))
        self.history = [h for h in history_rows if h["invoice_id"] == invoice_id]
        self.code = (daily.decide(self.line, vendor, self.name(manager_id), self.rules,
                                  self.history, today) if self.line else None)

    def allowed(self) -> list[str]:
        a = self.code.action if self.code else "skip"
        return {"handoff_anomaly": ["handoff_to_anomaly"], "reminder": ["send_reminder", "wait"],
                "escalate": ["escalate", "wait"]}.get(a, ["wait"])

    # -- read ---------------------------------------------------------------------------
    def get_invoice_context(self) -> dict:
        if self.line is None:
            return {"error": f"unknown invoice {self.invoice_id}"}
        pattern = next((p for p in vendor_patterns(self.rails, self.rule_rows, self.today,
                                                   self.manager_id) if p["vendor"] == self.vendor), None)
        c = self.code
        return {
            "today": self.today.isoformat(), "vendor": self.vendor,
            "ledger": self.line.model_dump(mode="json"),
            "agreement_rules": [{k: r.get(k) for k in ("rule_id", "rule_type", "status", "check",
                                                       "source_quote", "params")} for r in self.rules],
            "chaser_history": [{k: h.get(k) for k in ("as_of", "action", "level", "status",
                                                      "delivery", "delivery_reference",
                                                      "decided_by", "decision_note", "reason")}
                               for h in self.history if h["action"] != "reply_read"],
            "manager_replies": [{"on": h["as_of"], "reply": h.get("message"), "verdict": h["reason"]}
                                for h in self.history if h["action"] == "reply_read"],
            "vendor_history": describe(pattern),
            "manager_statements": self.statement_claims(),
            "policy": {"allowed": self.allowed(), "schedule_says": c.action if c else None,
                       "level": c.level if c else None, "due_date": c.due_date if c else None,
                       "days_overdue": c.days_overdue if c else 0,
                       "amount_owed": c.amount_owed if c else None,
                       "why": c.reason if c else "no ledger line"},
        }

    def statement_claims(self) -> list[dict]:
        """What Keyway's own statements say about this invoice (a claim, not proof)."""
        out = []
        for d in self.rails.documents(self.manager_id):
            if d.kind.value != "manager_statement" or (d.dated and d.dated > self.today):
                continue
            for row in d.data.get("lines", []):
                if row.get("invoice_id") == self.invoice_id:
                    out.append({"statement": d.id, "dated": str(d.dated), "says": row.get("status"),
                                "amount": row.get("amount"), "paid_on": row.get("on")})
        return out

    # -- decisions (terminal) -------------------------------------------------------------
    def _guard(self, tool: str) -> dict | None:
        if tool not in self.allowed():
            return {"error": f"{tool} is not allowed today; policy allows {self.allowed()} "
                             f"({self.code.reason if self.code else 'no context'})"}
        return None

    def _record(self, rationale: str) -> str:
        d = self.code
        self.record(d, f"{rationale} [schedule: {d.reason}]")
        return d.action_id()

    def send_reminder(self, rationale: str) -> dict:
        if err := self._guard("send_reminder"):
            return err
        aid = self._record(rationale)
        return {"recorded": aid, "level": self.code.level, "status": self.code.status,
                "message": self.code.message}

    def escalate(self, rationale: str) -> dict:
        if err := self._guard("escalate"):
            return err
        return {"recorded": self._record(rationale), "status": self.code.status,
                "notice": self.code.message}

    def handoff_to_anomaly(self, rationale: str) -> dict:
        if err := self._guard("handoff_to_anomaly"):
            return err
        aid = self._record(rationale)
        task = self.enqueue("anomaly", self.invoice_id, rationale,
                            {"handoff_action_id": aid, "deadline": self.code.due_date,
                             "rule_id": self.code.rule_id})
        return {"recorded": aid, "anomaly_task": task}

    def wait(self, rationale: str, until: str | None = None) -> dict:
        if until:
            try:
                if date.fromisoformat(until) <= self.today:
                    return {"error": "until must be after today"}
            except ValueError:
                return {"error": "until must be YYYY-MM-DD"}
        return {"waiting": True, "until": until, "schedule_says": self.code.action if self.code else None}

    def tools(self) -> list[Tool]:
        return [
            Tool("get_invoice_context", "Everything about this invoice: ledger line, agreement "
                 "rules with quotes, chaser history and controller decisions, manager replies, "
                 "vendor payment history, and `policy` (what the approved schedule allows today).",
                 _schema(), self.get_invoice_context),
            Tool("send_reminder", "Decision: send the next reminder on the approved ladder (the "
                 "level and template come from policy). Polite goes out automatically; firm and "
                 "escalation reminders wait for controller approval.",
                 _schema(rationale=RATIONALE), self.send_reminder, terminal=True),
            Tool("escalate", "Decision: escalate after all three reminders - notifies owner and "
                 "management, proposes holding the amount owed, opens a controller review.",
                 _schema(rationale=RATIONALE), self.escalate, terminal=True),
            Tool("handoff_to_anomaly", "Decision: a bank payment exists; hand the invoice to the "
                 "anomaly agent to check amount, recipient, account and timing.",
                 _schema(rationale=RATIONALE), self.handoff_to_anomaly, terminal=True),
            Tool("wait", "Decision: take no action today. Give the reason and, if known, the "
                 "date to look again.",
                 {"type": "object", "additionalProperties": False, "required": ["rationale"],
                  "properties": {"rationale": RATIONALE,
                                 "until": {"type": "string", "description": "YYYY-MM-DD"}}},
                 self.wait, terminal=True),
        ]


def run(tools: ChaserTools, client=None) -> Trace:
    task = (f"Task: chase invoice {tools.invoice_id} as of {tools.today.isoformat()}. Read its "
            "context, then make one decision.")
    return run_agent("chaser", SYSTEM, load_skill("chasing"), tools.tools(), task, client)


def outcome(trace: Trace) -> dict:
    d = trace.decision or {}
    return {"decision": d.get("tool"), "rationale": (d.get("input") or {}).get("rationale"),
            "result": d.get("result"), "error": trace.error}

