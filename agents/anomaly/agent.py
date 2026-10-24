"""Anomaly detector: runs only when a manager sends a financial statement.

It flags every statement line whose amount is not what Oakline approved to pay for that invoice,
and every line for an invoice Oakline never approved. The comparison is code. The LLM's only job
is reading a statement that arrives as free text: guards accept a line only if its invoice id and
amount are printed verbatim in the statement; otherwise the regex reader is used.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from contracts.models import AnomalyReport, Document, LedgerLine

from ..llm import StructuredLLM, guarded_call
from . import rules
from .prompts import SYSTEM


class ReadLine(BaseModel):
    line: str | None = Field(default=None, description="line label as printed, e.g. L14")
    invoice_id: str | None = Field(default=None, description="invoice number as printed")
    amount_text: str = Field(description="dollar amount exactly as printed, e.g. $1,240.00")


class StatementReading(BaseModel):
    lines: list[ReadLine]


def _amount(text: str) -> float | None:
    try:
        return float(text.replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


class AnomalyAgent:
    def __init__(self, llm: StructuredLLM | None = None):
        self.llm = llm

    def detect_anomalies(self, statement: Document, ledger: list[LedgerLine]) -> AnomalyReport:
        lines, how = rules.read_lines(statement)
        if how == "data" or self.llm is None:
            return rules.compare(statement, lines, ledger, how)

        def check(r: StatementReading) -> list[str]:
            p = []
            for ln in r.lines:
                if ln.invoice_id and ln.invoice_id not in statement.text:
                    p.append(f"invoice {ln.invoice_id!r} is not printed in the statement")
                if ln.amount_text not in statement.text or _amount(ln.amount_text) is None:
                    p.append(f"amount {ln.amount_text!r} is not printed in the statement")
            return p

        ans, _ = guarded_call(self.llm, StatementReading, SYSTEM,
                              f"<statement id=\"{statement.id}\">\n{statement.text}\n</statement>", check)
        if ans is None:
            return rules.compare(statement, lines, ledger, how)
        read = [{"line": ln.line, "invoice_id": ln.invoice_id, "amount": _amount(ln.amount_text)}
                for ln in ans.lines]
        return rules.compare(statement, read, ledger, "model")
