"""The chaser as a Claude agent: one task = one invoice, Claude chooses the next step.

Claude reads the invoice context with a tool, then makes exactly one decision with a decision
tool. The decision tools are guardrails: they accept only what the approved schedule allows today
(the deterministic check in daily.py, exposed to Claude as `policy`), so Claude can choose to wait
or explain, but cannot skip a reminder level, escalate early or hand off without bank evidence.
A hand-off creates a task for the anomaly agent.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

from . import daily
from .patterns import describe, vendor_patterns
from .runtime import Tool, Trace, load_skill, run_agent

def _sending() -> str:
    """How reminders leave today, from the live settings (never a stale description)."""
    order = daily.LEVEL_ORDER
    auto = [lv for lv in order if lv in daily.AUTO_LEVELS]
    held = [lv for lv in order if lv not in daily.AUTO_LEVELS]
    out = (f"{', '.join(auto)} reminders go out automatically" if auto else "no reminder goes out automatically")
    if held:
        out += f"; {', '.join(held)} reminders wait for controller approval"
    return out + ("; the escalation notice goes out automatically and only a hold needs the controller"
                  if daily.AUTO_ESCALATE else "; an escalation waits for the controller")


def system_prompt() -> str:
    return ("You are the chaser agent for Oakline Homes, a property owner. Oakline funds a management "
            "company (Keyway), which must pay each vendor by the agreement deadline. You handle one "
            "invoice per task: read its context and its evidence, flag any contradiction to the "
            "controller, then make exactly one decision using a decision tool. Follow the chasing "
            "skill. Base every statement on tool results; cite rule ids, document ids, dates and "
            f"amounts. You never write messages yourself: reminders are controller-approved templates. "
            f"Today {_sending()}.")


SYSTEM = system_prompt()

FLAG_KINDS = ("statement_claims_unbacked", "vendor_reports_unpaid", "reply_without_proof",
              "payment_to_other_vendor", "other_contradiction")

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
        line = next((l for l in rails.ledger(manager_id) if l.invoice_id == invoice_id), None)
        self.bank_rows = daily.bank_rows_by_invoice(rails, manager_id, today).get(invoice_id, [])
        self.line, self.paid_source = daily.verified_line(line, self.bank_rows) if line else (None, None)
        self.rule_rows = mirror.query("SELECT * FROM agreement_rule")
        vendor = self.name(self.line.vendor_id) if self.line else ""
        self.vendor = vendor
        self.rules = daily.rules_for(daily._rules_by_vendor(self.rule_rows), vendor, self.name(manager_id))
        self.history = [h for h in history_rows if h["invoice_id"] == invoice_id]
        self.code = (daily.decide(self.line, vendor, self.name(manager_id), self.rules,
                                  self.history, today, payment_seen=bool(self.bank_rows),
                                  paid_source=self.paid_source) if self.line else None)
        self.evidence_ids: set[str] = set()

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

    def get_evidence(self) -> dict:
        """Every document filed so far about this invoice, newest last: the invoice, Oakline's
        funding, Keyway's bank rows (with the vendor each paid), vendor confirmations and
        notices, Keyway's replies and statement lines."""
        out = []
        for d in self.rails.documents(self.manager_id):
            if d.dated and d.dated > self.today:
                continue
            data = d.data or {}
            if data.get("invoice_id") == self.invoice_id:
                detail = {k: v for k, v in data.items() if k != "invoice_id"}
            elif d.kind.value == "manager_bank":
                detail = [p for p in data.get("payments", []) if p.get("invoice_id") == self.invoice_id]
            elif d.kind.value == "manager_statement":
                detail = [r for r in data.get("lines", []) if r.get("invoice_id") == self.invoice_id]
            else:
                continue
            if not detail:
                continue
            if d.kind.value == "manager_bank":
                for p in detail:
                    p["vendor"] = self.name(p["vendor_id"]) if p.get("vendor_id") else None
                    p["to_this_invoices_vendor"] = p.get("vendor_id") in (None, self.line.vendor_id)
            out.append({"document": d.id, "kind": d.kind.value, "dated": str(d.dated),
                        "text": (d.text or "")[:300], "detail": detail})
        out.sort(key=lambda x: (x["dated"], x["document"]))
        self.evidence_ids = {x["document"] for x in out}
        return {"invoice_id": self.invoice_id, "documents": out,
                "counted_payment": {"amount": self.line.paid_amount, "on": str(self.line.paid_on),
                                    "source": self.paid_source}}

    def flag_for_controller(self, kind: str, evidence_ids: list[str], rationale: str) -> dict:
        """Open a controller review for a contradiction between sources. Not a decision: the
        reminder schedule goes on; the controller sees the flag with its cited documents."""
        if self.code is None:
            return {"error": "no ledger line for this invoice"}
        if kind not in FLAG_KINDS:
            return {"error": f"kind must be one of {list(FLAG_KINDS)}"}
        if not self.evidence_ids:
            self.get_evidence()
        missing = [e for e in evidence_ids if e not in self.evidence_ids]
        if not evidence_ids or missing:
            return {"error": f"cite documents from get_evidence; unknown: {missing or 'none given'}"}
        flag = replace(self.code, action="flag", level=kind, status="needs_controller",
                       message=rationale, attempt=0)
        if any(h["action_id"] == flag.action_id() for h in self.history):
            return {"already_flagged": flag.action_id()}
        self.record(flag, f"{rationale} [evidence: {', '.join(evidence_ids)}]")
        from .runtime import emit
        emit({"type": "flag_raised", "kind": kind, "evidence": evidence_ids, "summary": rationale[:400]})
        return {"flagged": flag.action_id(), "status": "needs_controller"}

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
            Tool("get_evidence", "Every document about this invoice so far: invoice, funding, "
                 "Keyway bank rows (and which vendor each paid), vendor confirmations and notices, "
                 "Keyway's replies, statement lines. Use it to check claims against each other.",
                 _schema(), self.get_evidence),
            Tool("flag_for_controller", "Open a controller review when sources disagree (a statement "
                 "or reply claims payment the bank does not show, a vendor says it was not paid, a "
                 "payment went to another vendor). Cite document ids from get_evidence. Not a "
                 "decision: still make one decision afterwards.",
                 _schema(kind={"type": "string", "enum": list(FLAG_KINDS)},
                         evidence_ids={"type": "array", "items": {"type": "string"}, "minItems": 1},
                         rationale=RATIONALE),
                 self.flag_for_controller),
            Tool("send_reminder", "Decision: send the next reminder on the approved ladder (the "
                 f"level and template come from policy). {_sending().capitalize()}.",
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
    return run_agent("chaser", system_prompt(), load_skill("chasing"), tools.tools(), task, client)


def outcome(trace: Trace) -> dict:
    d = trace.decision or {}
    return {"decision": d.get("tool"), "rationale": (d.get("input") or {}).get("rationale"),
            "result": d.get("result"), "error": trace.error}

