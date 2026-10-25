"""The anomaly agent: Claude reviews a payment the chaser handed off and records a verdict.

The automatic checks (chaser_agent/connected.py) compute the findings - recipient, account,
amount, timing, statement. Claude reads them with tools, interprets them for the controller and
records one verdict. `record_verdict` is a guardrail: a verdict the findings do not support is
refused (no `clean` with open findings, no `awaiting_statement` when money is wrong). The verdict
closes the chaser's hand-off and the case goes to the controller.
"""
from __future__ import annotations

from chaser_agent import daily, memory
from chaser_agent.patterns import describe, vendor_patterns
from chaser_agent.runtime import Tool, Trace, load_skill, run_agent
from workflow.names import names_from

SYSTEM = """You are the anomaly agent for Oakline Homes, a property owner whose management \
company (Keyway) pays vendors with money Oakline funds. The chaser agent handed you one invoice \
because a bank payment appeared. Check that the payment is right - recipient, account, amount, \
timing and the management statement - and record exactly one verdict for the controller. \
Follow the payment_review skill. Base every statement on tool results and cite finding codes, \
dates and amounts. You do not close cases or move money; the controller decides."""

STATEMENT_ONLY = {"statement_predates_payment", "statement_payment_unconfirmed",
                  "statement_mismatch"}
VERDICTS = ("clean", "awaiting_statement", "needs_review")


class AnomalyTools:
    def __init__(self, rails, mirror, invoice_id: str, case: dict | None, documents: list,
                 today, close_handoff, manager_id: str = "keyway"):
        self.rails, self.mirror, self.invoice_id, self.case = rails, mirror, invoice_id, case
        self.documents, self.today, self.close_handoff = documents, today, close_handoff
        self.manager_id = manager_id
        self.rule_rows = mirror.query("SELECT * FROM agreement_rule")

    def _codes(self) -> set[str]:
        return {f["code"] for f in (self.case or {}).get("findings", [])}

    def get_payment_case(self) -> dict:
        if self.case is None:
            return {"error": f"no workflow case for {self.invoice_id}"}
        bank = [{"document": d.id, **p} for d in self.documents if d.kind.value == "manager_bank"
                for p in d.data.get("payments", [])]
        statements = [{"document": d.id, "dated": str(d.dated), "lines": d.data.get("lines", [])}
                      for d in self.documents if d.kind.value == "manager_statement"]
        line = next((l for l in self.rails.ledger(self.manager_id)
                     if l.invoice_id == self.invoice_id), None)
        return {"invoice_id": self.invoice_id, "vendor": self.case.get("vendor"),
                "case_state": self.case["state"], "findings": self.case["findings"],
                "closure_eligible": self.case.get("closure_eligible", False),
                "ledger": line.model_dump(mode="json") if line else None,
                "bank_payments": bank, "statements": statements,
                "sources": self.case.get("source_ids", []),
                # Memory: your earlier verdicts on this invoice, and lessons about the vendor and
                # the manager.
                "your_previous_runs": memory.previous_runs(self.mirror, "anomaly", self.invoice_id, self.today),
                "lessons": memory.lessons(self.mirror, memory.book_of(self.rails),
                                          [self.case.get("vendor") or "", names_from(self.rails)(self.manager_id)])}

    def get_agreement_rules(self) -> dict:
        from workflow.names import names_from
        manager = names_from(self.rails)(self.manager_id)
        own = daily.rules_for(daily._rules_by_vendor(self.rule_rows), (self.case or {}).get("vendor", ""), manager)
        rules = [{k: r.get(k) for k in ("rule_id", "rule_type", "status", "check", "source_quote")}
                 for r in own]
        return {"rules": rules} if rules else {"error": "no saved agreement for this vendor"}

    def vendor_history(self) -> dict:
        vendor = (self.case or {}).get("vendor")
        p = next((x for x in vendor_patterns(self.rails, self.rule_rows, self.today,
                                             self.manager_id) if x["vendor"] == vendor), None)
        return {"vendor": vendor, "summary": describe(p),
                "late_invoices": (p or {}).get("late_invoices", [])}

    def record_verdict(self, verdict: str, summary: str, finding_codes: list[str]) -> dict:
        codes = self._codes()
        if verdict not in VERDICTS:
            return {"error": f"verdict must be one of {VERDICTS}"}
        if not set(finding_codes) <= codes:
            return {"error": f"unknown finding codes {sorted(set(finding_codes) - codes)}; "
                             f"the case has {sorted(codes)}"}
        if verdict == "clean" and (codes or not (self.case or {}).get("closure_eligible")):
            return {"error": f"cannot be clean: open findings {sorted(codes)} or not eligible to close"}
        if verdict == "awaiting_statement" and (not codes or codes - STATEMENT_ONLY):
            return {"error": "awaiting_statement only fits statement findings; money findings "
                             f"need review: {sorted(codes - STATEMENT_ONLY)}"}
        if verdict == "needs_review" and not codes:
            return {"error": "needs_review requires at least one finding"}
        missing = sorted(codes - set(finding_codes))
        if missing:
            return {"error": f"cite every finding the case has; missing {missing}"}
        self.close_handoff(verdict, summary)
        return {"verdict": verdict, "handoff": "reviewed",
                "next": {"clean": "controller may resolve the case",
                         "awaiting_statement": "statement request is queued to management",
                         "needs_review": "controller review is open"}[verdict]}

    def tools(self) -> list[Tool]:
        from chaser_agent import books
        no_args = {"type": "object", "additionalProperties": False, "properties": {}}
        return [
            books.tool_for(self.rails),
            memory.tool_for(self.mirror, self.rails, "anomaly", self.today),
            Tool("get_payment_case", "The handed-off payment: automatic check findings (codes and "
                 "messages), bank rows, statements, ledger line, closure eligibility.",
                 no_args, self.get_payment_case),
            Tool("get_agreement_rules", "The vendor's agreement rules with the quote each came from.",
                 no_args, self.get_agreement_rules),
            Tool("vendor_history", "How often this vendor has been paid late before.",
                 no_args, self.vendor_history),
            Tool("record_verdict", "Decision: record the verdict for the controller. Refused when "
                 "the findings do not support it.",
                 {"type": "object", "additionalProperties": False,
                  "required": ["verdict", "summary", "finding_codes"],
                  "properties": {
                      "verdict": {"type": "string", "enum": list(VERDICTS)},
                      "summary": {"type": "string", "minLength": 20, "maxLength": 1500,
                                  "description": "2-4 neutral sentences for the controller"},
                      "finding_codes": {"type": "array", "items": {"type": "string"}}}},
                 self.record_verdict, terminal=True),
        ]


def run(tools: AnomalyTools, reason: str, client=None) -> Trace:
    task = (f"Task: review the payment for invoice {tools.invoice_id} as of "
            f"{tools.today.isoformat()}. The chaser handed it off: {reason}")
    return run_agent("anomaly", SYSTEM, load_skill("payment_review"), tools.tools(), task, client)


def outcome(trace: Trace) -> dict:
    d = trace.decision or {}
    i = d.get("input") or {}
    return {"decision": d.get("tool"), "verdict": i.get("verdict"), "summary": i.get("summary"),
            "finding_codes": i.get("finding_codes"), "result": d.get("result"),
            "error": trace.error}
