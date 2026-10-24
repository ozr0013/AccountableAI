"""Anomaly detector by rules: statement lines against the amounts Oakline approved to pay.

Comparing money is code, always. The only judgment is reading a statement that arrives as text
with no structured lines; the regex reader here is the free fallback for that.
"""

from __future__ import annotations

import re

from contracts.models import AnomalyReport, Citation, Document, LedgerLine, SourceKind, StatementAnomaly

# "L14: 03/10 Rio Grande Plumbing INV-KW-0301 $1,240.00 PAID"
_LINE = re.compile(r"(?:\b(L\d+)\s*:?\s*)?[^;\n]*?\b(INV-[A-Z]{2}-\d{3,4})\b[^$;\n]*?\$\s?([\d,]+(?:\.\d{2})?)")


def cents(x: float) -> int:
    return round(x * 100)


def read_lines(statement: Document) -> tuple[list[dict], str]:
    """Statement lines as [{line, invoice_id, amount}], and how they were read ("data" | "rules")."""
    if statement.data.get("lines"):
        return [{"line": ln.get("line"), "invoice_id": ln.get("invoice_id"),
                 "amount": float(ln.get("amount", 0))} for ln in statement.data["lines"]], "data"
    out = [{"line": m.group(1), "invoice_id": m.group(2), "amount": float(m.group(3).replace(",", ""))}
           for m in _LINE.finditer(statement.text)]
    return out, "rules"


def compare(statement: Document, lines: list[dict], ledger: list[LedgerLine],
            read_by: str = "data") -> AnomalyReport:
    """Every line whose amount differs from the approved invoice amount, or that names an
    invoice Oakline never approved, is an anomaly. A matching amount is not."""
    by_id = {l.invoice_id: l for l in ledger}
    anomalies, matched = [], []
    for ln in lines:
        iid, amount = ln.get("invoice_id"), float(ln.get("amount") or 0)
        cite = Citation(source_id=statement.id, kind=SourceKind.manager_statement,
                        locator=ln.get("line"))
        ours = by_id.get(iid) if iid else None
        if ours is None:
            anomalies.append(StatementAnomaly(
                invoice_id=iid, line=ln.get("line"), statement_amount=amount, expected_amount=0.0,
                difference=round(amount, 2), reason="not_on_ledger", citation=cite))
        elif cents(amount) != cents(ours.amount):
            anomalies.append(StatementAnomaly(
                invoice_id=iid, line=ln.get("line"), statement_amount=amount,
                expected_amount=ours.amount, funded_amount=ours.funded_amount,
                difference=round(amount - ours.amount, 2), reason="amount_differs", citation=cite))
        else:
            matched.append(iid)
    return AnomalyReport(statement_id=statement.id, manager_id=statement.manager_id or "",
                         checked=len(lines), anomalies=anomalies, matched=matched, read_by=read_by)


def detect(statement: Document, ledger: list[LedgerLine]) -> AnomalyReport:
    lines, how = read_lines(statement)
    return compare(statement, lines, ledger, how)
