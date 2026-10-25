"""The investigator: a daily sweep across every invoice and bank payment, with Claude writing
its own read-only SQL over the books.

It looks for what per-invoice checks cannot see (a payment with no invoice number, one transfer
covering several invoices, money booked under the wrong number), proves each finding with a
second query, and then either opens a controller review citing the documents or hands the
invoice to the chaser with a note. It never chases, never sends anything and never moves money.
"""
from __future__ import annotations

import os
from datetime import date

from . import books, daily, memory
from .runtime import Tool, Trace, emit, load_skill, run_agent

SYSTEM = ("You are the investigator agent for Oakline Homes. Oakline funds a management company "
          "(Keyway) that pays the vendors. Each day you sweep all of Keyway's invoices and bank "
          "payments with your own read-only SQL, looking for problems no single invoice shows. "
          "Follow the investigating skill. Every claim must come from a query result; cite "
          "document ids, invoice ids, dates and amounts. You raise cases and hand work on; you "
          "never chase, send messages or move money.")

FLAG_KINDS = ("unreferenced_payment", "bundled_payment", "misapplied_payment", "duplicate_payment",
              "statement_claims_unbacked", "vendor_pattern", "other_contradiction")
MAX_TURNS = 16
RATIONALE = {"type": "string", "minLength": 20, "maxLength": 1500,
             "description": "2-4 neutral sentences: what the documents show and why it matters"}


class InvestigatorTools:
    def __init__(self, rails, mirror, today: date, record, enqueue, manager_id: str = "keyway"):
        from workflow.names import names_from
        self.rails, self.mirror, self.today = rails, mirror, today
        self.record, self.enqueue, self.manager_id = record, enqueue, manager_id
        self.name = names_from(rails)
        self.flags: list[str] = []
        self.handed: list[str] = []

    def _known(self, table: str, column: str, values: list[str]) -> set[str]:
        return books.known(self.rails, table, column, values)

    def _line(self, invoice_id: str):
        return next((l for l in self.rails.ledger(self.manager_id) if l.invoice_id == invoice_id), None)

    def flag_for_controller(self, invoice_id: str, kind: str, evidence_ids: list[str],
                            rationale: str) -> dict:
        """Open a controller review on one invoice, citing documents that exist in the books."""
        if kind not in FLAG_KINDS:
            return {"error": f"kind must be one of {list(FLAG_KINDS)}"}
        line = self._line(invoice_id)
        if line is None:
            return {"error": f"unknown invoice {invoice_id}"}
        missing = sorted(set(evidence_ids) - self._known("documents", "document_id", evidence_ids))
        if not evidence_ids or missing:
            return {"error": f"cite document ids that exist in the books; unknown: {missing or 'none given'}"}
        flag = daily.Decision(invoice_id=invoice_id, vendor_name=self.name(line.vendor_id), action="flag",
                              level=kind, status="needs_controller", attempt=0, message=rationale,
                              reason=f"{rationale} [evidence: {', '.join(evidence_ids)}] [by: investigator]")
        from . import api
        if any(h["action_id"] == flag.action_id() for h in api.history(self.mirror)):
            return {"already_flagged": flag.action_id()}
        self.record(flag, flag.reason)
        self.flags.append(flag.action_id())
        emit({"type": "flag_raised", "invoice_id": invoice_id, "kind": kind, "evidence": evidence_ids,
              "summary": rationale[:400]})
        return {"flagged": flag.action_id(), "status": "needs_controller"}

    def hand_to_chaser(self, invoice_id: str, note: str) -> dict:
        """Queue today's chaser task for this invoice with your note attached."""
        if self._line(invoice_id) is None:
            return {"error": f"unknown invoice {invoice_id}"}
        task = self.enqueue("chaser", invoice_id, f"Note from the investigator: {note}",
                            {"note": note}, self.today, "investigator")
        self.handed.append(invoice_id)
        return {"queued": task}

    def finish_sweep(self, summary: str, checked: list[str]) -> dict:
        return {"summary": summary, "checked": checked, "flags": self.flags, "handed_to_chaser": self.handed}

    def tools(self) -> list[Tool]:
        return [
            books.tool_for(self.rails),
            memory.tool_for(self.mirror, self.rails, "investigator", self.today),
            Tool("flag_for_controller", "Open a controller review on one invoice. Cite document ids "
                 "you found with query_books; uncited or repeated flags are refused.",
                 {"type": "object", "additionalProperties": False,
                  "required": ["invoice_id", "kind", "evidence_ids", "rationale"],
                  "properties": {"invoice_id": {"type": "string"},
                                 "kind": {"type": "string", "enum": list(FLAG_KINDS)},
                                 "evidence_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                                 "rationale": RATIONALE}},
                 self.flag_for_controller),
            Tool("hand_to_chaser", "Hand an invoice to the chaser today with a note it will read "
                 "(for example: a bank row without an invoice number seems to pay it).",
                 {"type": "object", "additionalProperties": False, "required": ["invoice_id", "note"],
                  "properties": {"invoice_id": {"type": "string"},
                                 "note": {"type": "string", "minLength": 10, "maxLength": 600}}},
                 self.hand_to_chaser),
            Tool("finish_sweep", "Decision: end today's sweep with what you checked and found.",
                 {"type": "object", "additionalProperties": False, "required": ["summary", "checked"],
                  "properties": {"summary": RATIONALE,
                                 "checked": {"type": "array", "items": {"type": "string"}}}},
                 self.finish_sweep, terminal=True),
        ]


def briefing(tools: InvestigatorTools) -> str:
    """The investigator's memory: its last sweeps, its flags still open, and the lessons on file.
    Continue from here; do not redo work already done unless new documents arrived."""
    import json
    mem = {"your_previous_sweeps": memory.previous_runs(tools.mirror, "investigator", "portfolio",
                                                        tools.today, n=memory.SWEEPS_RECALLED),
           "your_open_flags": memory.open_flags(tools.mirror),
           "lessons": memory.lessons(tools.mirror, memory.book_of(tools.rails))}
    return ("Your memory (from earlier days; the books are the truth if they disagree):\n"
            + json.dumps(mem, default=str)[:6000])


def run(tools: InvestigatorTools, client=None, lead: str | None = None) -> Trace:
    if lead:
        # Someone told us something: a lead to verify against the books, never a fact by itself.
        task = (f"Task: today is {tools.today.isoformat()}. {lead}\nVerify it against the books with "
                "query_books: say whether the documents support it, contradict it, or are silent. Flag "
                "or hand on only what the documents prove, then finish_sweep with your conclusion.\n"
                + briefing(tools))
    else:
        task = (f"Task: today is {tools.today.isoformat()}. Sweep Keyway's books for problems no single "
                "invoice shows. Investigate with query_books, flag or hand on what you prove, then "
                "finish_sweep.\n" + briefing(tools))
    # INVESTIGATOR_EFFORT=low makes each daily sweep quicker, for a time-boxed showing.
    return run_agent("investigator", SYSTEM, load_skill("investigating"), tools.tools(), task,
                     client, effort=os.getenv("INVESTIGATOR_EFFORT", "medium"), max_turns=MAX_TURNS)


def outcome(trace: Trace) -> dict:
    d = trace.decision or {}
    result = d.get("result") or {}
    return {"decision": d.get("tool"), "summary": (d.get("input") or {}).get("summary"),
            "flags": result.get("flags", []), "handed_to_chaser": result.get("handed_to_chaser", []),
            "result": result, "error": trace.error}
