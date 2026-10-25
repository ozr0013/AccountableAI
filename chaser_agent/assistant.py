"""Ask the chaser: Claude answers questions using read-only tools over the live data.

Claude decides which tools to call (and in what order) to answer; every tool only reads.
Nothing here can send a reminder, approve, change a rule or move money: those stay with the
daily run and the controller. Each answer comes back with the tools Claude called, what they
returned (truncated), and a summary of its reasoning, so the answer can be checked.
"""
from __future__ import annotations

import json
import os
from datetime import date

from . import daily
from .patterns import vendor_patterns

MODEL = os.getenv("CHASER_MODEL", "claude-opus-5-5")
MAX_TURNS = 8
RESULT_LIMIT = 12000

SYSTEM = """You are the chaser assistant for Oakline Homes, a property owner. Oakline funds a \
management company (Keyway), which pays the vendors. You answer the controller's questions about \
vendor invoices, payment deadlines, how often vendors are paid late, and what the chaser has done.

How to work:
- Answer only from tool results. Call tools to look things up; never guess an amount, date, \
deadline or status. If the tools don't have it, say so.
- Deadlines come from each vendor's agreement rules (pay_within_days); a payment counts only \
when a bank record shows it.
- Cite what you rely on: invoice ids, rule ids, dates and amounts from the tool results.
- You can only read. If asked to send, approve, hold or change something, explain what the \
daily chaser or the controller would do instead.
- Neutral wording: never accuse anyone. The strongest word for missing money is "unexplained".
- Keep the answer short: a direct answer first, then the supporting facts."""

TOOLS = [
    {"name": "list_invoices",
     "description": "List invoices with amount, receipt date, deadline, amount paid and status. "
                    "Filter by status (overdue = past deadline and unpaid) and/or vendor name.",
     "input_schema": {"type": "object", "additionalProperties": False, "properties": {
         "status": {"type": "string", "enum": ["all", "overdue", "unpaid", "paid", "not_due"]},
         "vendor": {"type": "string", "description": "vendor name, partial match"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 50}}}},
    {"name": "get_invoice",
     "description": "One invoice in full: ledger line, the chaser's decision for today, the "
                    "vendor's agreement rules, and every chaser action and controller decision.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["invoice_id"],
                      "properties": {"invoice_id": {"type": "string"}}}},
    {"name": "vendor_lateness",
     "description": "How regularly each vendor is paid late: paid count, late count, late rate, "
                    "average and max days late, overdue unpaid invoices, chronic flag. Sorted "
                    "worst first. Optionally one vendor.",
     "input_schema": {"type": "object", "additionalProperties": False, "properties": {
         "vendor": {"type": "string", "description": "vendor name, partial match"}}}},
    {"name": "agreement_rules",
     "description": "The saved agreement and its payment rules for a vendor (deadline, amount, "
                    "payee, account, holds) with the quote each rule came from.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["vendor"],
                      "properties": {"vendor": {"type": "string"}}}},
    {"name": "chaser_actions",
     "description": "Recorded chaser actions (reminders, escalations, hand-offs, holds, replies) "
                    "with their status, optionally for one invoice or one status.",
     "input_schema": {"type": "object", "additionalProperties": False, "properties": {
         "invoice_id": {"type": "string"},
         "status": {"type": "string", "enum": ["queued", "needs_approval", "needs_controller",
                                                "approved", "rejected", "recorded"]}}}},
    {"name": "preview_daily_check",
     "description": "Dry run of the chaser's daily check as of a date (default today): what it "
                    "would do for each invoice. Saves nothing.",
     "input_schema": {"type": "object", "additionalProperties": False, "properties": {
         "as_of": {"type": "string", "description": "YYYY-MM-DD"}}}},
]


class DataTools:
    """The read-only implementations behind TOOLS."""

    def __init__(self, rails, mirror, history_fn, today: date, manager_id: str = "keyway"):
        from workflow.names import names_from
        self.rails, self.mirror, self.history_fn = rails, mirror, history_fn
        self.today, self.manager_id, self.name = today, manager_id, names_from(rails)

    def _rules(self):
        return self.mirror.query("SELECT * FROM agreement_rule")

    def _decisions(self, as_of: date | None = None):
        return {d.invoice_id: d for d in daily.run(self.rails, self._rules(), self.history_fn(),
                                                   as_of or self.today, self.manager_id)}

    def list_invoices(self, status: str = "all", vendor: str | None = None, limit: int = 20):
        decisions = self._decisions()
        rows = []
        for line in self.rails.ledger(self.manager_id):
            name = self.name(line.vendor_id)
            if vendor and vendor.lower() not in name.lower():
                continue
            d = decisions.get(line.invoice_id)
            paid = line.paid_amount >= line.amount > 0
            state = ("paid" if paid else "overdue" if d and d.days_overdue > 0
                     else "not_due" if d and d.action == "not_due" else "unpaid")
            if status not in ("all", state) and not (status == "unpaid" and state == "overdue"):
                continue
            rows.append({"invoice_id": line.invoice_id, "vendor": name, "amount": line.amount,
                         "received": str(line.invoice_received), "paid_amount": line.paid_amount,
                         "paid_on": str(line.paid_on) if line.paid_on else None,
                         "deadline": d.due_date if d else (str(line.due_date) if line.due_date else None),
                         "status": state, "chaser_today": d.action if d else None})
        return {"count": len(rows), "invoices": rows[:limit]}

    def get_invoice(self, invoice_id: str):
        line = next((l for l in self.rails.ledger(self.manager_id) if l.invoice_id == invoice_id), None)
        if line is None:
            return {"error": f"unknown invoice {invoice_id}"}
        vendor = self.name(line.vendor_id)
        d = self._decisions().get(invoice_id)
        return {"ledger": line.model_dump(mode="json"), "vendor": vendor,
                "chaser_today": d.to_dict() if d else None,
                "rules": [r for r in self._rules()
                          if daily.norm_name(r["vendor_name"]) == daily.norm_name(vendor)],
                "actions": [h for h in self.history_fn() if h["invoice_id"] == invoice_id]}

    def vendor_lateness(self, vendor: str | None = None):
        rows = vendor_patterns(self.rails, self._rules(), self.today, self.manager_id)
        return [r for r in rows if not vendor or vendor.lower() in r["vendor"].lower()]

    def agreement_rules(self, vendor: str):
        key = daily.norm_name(vendor)
        agreements = [a for a in self.mirror.query("SELECT * FROM agreement")
                      if key and key in daily.norm_name(a.get("vendor_name") or "")]
        rules = [r for r in self._rules() if key and key in daily.norm_name(r["vendor_name"])]
        slim = [{k: a.get(k) for k in ("doc_id", "filename", "status", "payment_due",
                                        "payment_amount", "payment_info", "confirmed_by")}
                for a in agreements]
        return {"agreements": slim, "rules": rules} if slim or rules else {
            "error": f"no saved agreement for {vendor}"}

    def chaser_actions(self, invoice_id: str | None = None, status: str | None = None):
        return [h for h in self.history_fn()
                if invoice_id in (None, h["invoice_id"]) and status in (None, h["status"])]

    def preview_daily_check(self, as_of: str | None = None):
        try:
            day = date.fromisoformat(as_of) if as_of else self.today
        except ValueError:
            return {"error": "as_of must be YYYY-MM-DD"}
        return [d.to_dict() for d in self._decisions(day).values() if d.action != "skip"]

    def call(self, name: str, args: dict):
        if name not in {t["name"] for t in TOOLS}:
            return {"error": f"unknown tool {name}"}
        if not isinstance(args, dict):
            return {"error": "tool input must be an object"}
        try:
            return getattr(self, name)(**args)
        except TypeError as exc:
            return {"error": f"bad arguments: {exc}"}


def _client():
    import anthropic
    return anthropic.Anthropic()


def ask(question: str, tools: DataTools, client=None) -> dict:
    """Run Claude over the read-only tools until it answers (at most MAX_TURNS model calls)."""
    client = client or _client()
    messages = [{"role": "user", "content": f"Today is {tools.today.isoformat()}.\n\n{question}"}]
    trace, thinking, usage = [], [], {"input_tokens": 0, "output_tokens": 0}
    for _ in range(MAX_TURNS):
        resp = client.beta.messages.create(
            model=MODEL, max_tokens=16000, system=SYSTEM, tools=TOOLS, messages=messages,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": "medium"},
            cache_control={"type": "ephemeral"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        usage["input_tokens"] += resp.usage.input_tokens or 0
        usage["output_tokens"] += resp.usage.output_tokens or 0
        thinking += [b.thinking for b in resp.content if b.type == "thinking" and b.thinking]
        if resp.stop_reason == "refusal":
            return {"answer": None, "error": "the model declined this question", "tools": trace,
                    "thinking": thinking, "usage": usage, "model": resp.model}
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not uses:
            answer = "\n".join(b.text for b in resp.content if b.type == "text").strip()
            return {"answer": answer, "tools": trace, "thinking": thinking, "usage": usage,
                    "model": resp.model}
        results = []
        for block in uses:
            out = tools.call(block.name, block.input)
            text = json.dumps(out, default=str)
            if len(text) > RESULT_LIMIT:
                text = text[:RESULT_LIMIT] + ' ... "[truncated: narrow the query]"'
            trace.append({"tool": block.name, "input": block.input, "result_preview": text[:400]})
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": text,
                            "is_error": isinstance(out, dict) and "error" in out})
        messages.append({"role": "user", "content": results})
    return {"answer": None, "error": f"no answer within {MAX_TURNS} steps", "tools": trace,
            "thinking": thinking, "usage": usage, "model": MODEL}
