"""Anomaly detector by rules: statement lines against the amounts Oakline approved to pay.

Comparing money is code, always. The only judgment is reading a statement that arrives as text
with no structured lines; the regex reader here is the free fallback for that.

Per invoice, not per line: a statement may show one invoice in installments (two lines of $620
for a $1,240 invoice), and a stated holdback ("$200 retained") counts toward the approved amount.
Given the manager's bank lines too, every invoice the statement calls paid is checked against
what the bank actually paid out for it.
"""

from __future__ import annotations

import re
from collections import defaultdict

from contracts.models import (
    AnomalyReport,
    Citation,
    Document,
    LedgerLine,
    SourceKind,
    StatementAnomaly,
)

# "L14: 03/10 Rio Grande Plumbing INV-KW-0301 $1,240.00 PAID"
_LINE = re.compile(r"(?:\b(L\d+)\s*:?\s*)?[^;\n]*?\b(INV-[A-Z]{2}-\d{3,4})\b[^$;\n]*?\$\s?([\d,]+(?:\.\d{2})?)")


def cents(x: float) -> int:
    return round(x * 100)


def _key(ref: str) -> str:
    """An invoice number for comparison: 'INV-G-0291', 'G0291' and 'G-0291' are the same."""
    return re.sub(r"[^A-Z0-9]", "", re.sub(r"^INV-", "", (ref or "").upper()))


def read_lines(statement: Document) -> tuple[list[dict], str]:
    """Statement lines as [{line, invoice_id, amount, ...}], and how they were read ("data" | "rules")."""
    if statement.data.get("lines"):
        return [{**ln, "amount": float(ln.get("amount", 0))} for ln in statement.data["lines"]], "data"
    out = [{"line": m.group(1), "invoice_id": m.group(2), "amount": float(m.group(3).replace(",", ""))}
           for m in _LINE.finditer(statement.text)]
    return out, "rules"


def _resolve(lines: list[dict], ledger: list[LedgerLine]) -> list[dict]:
    """Give each line an invoice id: its own, its printed reference, or (no reference) the one
    invoice of that vendor with that amount that no other line names."""
    by_key = {_key(l.invoice_id): l.invoice_id for l in ledger}
    out = []
    for ln in lines:
        iid = ln.get("invoice_id")
        if iid and iid not in {l.invoice_id for l in ledger} and _key(iid) in by_key:
            iid = by_key[_key(iid)]
        if not iid and ln.get("_vendor_ref"):
            iid = by_key.get(_key(ln["_vendor_ref"]))
        out.append({**ln, "invoice_id": iid})
    named = {ln["invoice_id"] for ln in out if ln["invoice_id"]}
    for ln in out:
        if ln["invoice_id"] or not ln.get("_vendor_id"):
            continue
        cands = [l.invoice_id for l in ledger if l.vendor_id == ln["_vendor_id"]
                 and cents(l.amount) == cents(ln["amount"]) and l.invoice_id not in named]
        if len(cands) == 1:
            ln["invoice_id"] = cands[0]
            named.add(cands[0])
    return out


def _bank_paid(iid: str, line: LedgerLine, bank: list[dict], refs: dict[str, list[str]],
               stated: list[float]) -> float:
    """What the bank paid out for this invoice: the vendor's debits that carry its number or a
    known reference (a job ref), else the vendor's debits equal to a statement line's amount."""
    keys = {_key(iid), *(_key(r) for r in refs.get(iid, []))}
    mine = [p for p in bank if p.get("vendor_id") == line.vendor_id]
    tagged = [p for p in mine if keys & {_key(n) for n in p.get("_nums", [])}]
    if tagged:
        return round(sum(p["amount"] for p in tagged), 2)
    untagged = [p for p in mine if not p.get("_nums")]
    hits, pool = [], list(untagged)
    for amt in stated:
        m = next((p for p in pool if cents(p["amount"]) == cents(amt)), None)
        if m:
            hits.append(m)
            pool.remove(m)
    return round(sum(p["amount"] for p in hits), 2)


def compare(statement: Document, lines: list[dict], ledger: list[LedgerLine],
            read_by: str = "data", bank: list[dict] | None = None,
            refs: dict[str, list[str]] | None = None) -> AnomalyReport:
    """Each invoice on the statement, all its lines together, against what Oakline approved; and,
    given bank lines, each invoice called paid against what the bank paid out for it."""
    by_id = {l.invoice_id: l for l in ledger}
    lines = _resolve(lines, ledger)
    groups: dict[str, list[dict]] = defaultdict(list)
    anomalies, matched = [], []
    for ln in lines:
        if ln.get("invoice_id") in by_id:
            groups[ln["invoice_id"]].append(ln)
            continue
        cite = Citation(source_id=statement.id, kind=SourceKind.manager_statement, locator=ln.get("line"))
        amount = float(ln.get("amount") or 0)
        anomalies.append(StatementAnomaly(
            invoice_id=ln.get("invoice_id"), line=ln.get("line"), statement_amount=amount,
            expected_amount=0.0, difference=round(amount, 2), reason="not_on_ledger", citation=cite))
    for iid, lns in groups.items():
        ours = by_id[iid]
        stated = round(sum(float(ln.get("amount") or 0) for ln in lns), 2)
        retained = round(sum(float(ln.get("retained") or 0) for ln in lns), 2)
        loc = ",".join(ln["line"] for ln in lns if ln.get("line")) or None
        cite = Citation(source_id=statement.id, kind=SourceKind.manager_statement, locator=loc)
        statuses = {str(ln.get("status") or "").lower() for ln in lns}
        flagged = False
        if cents(stated + retained) != cents(ours.amount):
            anomalies.append(StatementAnomaly(
                invoice_id=iid, line=loc, statement_amount=stated, expected_amount=ours.amount,
                funded_amount=ours.funded_amount, difference=round(stated + retained - ours.amount, 2),
                reason="amount_differs", citation=cite, retained=retained))
            flagged = True
        elif ours.amount > 0 and cents(ours.paid_amount) >= cents(ours.amount) and "paid" not in statuses:
            anomalies.append(StatementAnomaly(
                invoice_id=iid, line=loc, statement_amount=stated, expected_amount=ours.amount,
                difference=0.0, reason="already_paid", citation=cite,
                note=f"billed again, but already paid ${ours.paid_amount:,.2f}"))
            flagged = True
        if bank is not None and "paid" in statuses:
            paid_lines = [float(ln.get("amount") or 0) for ln in lns if str(ln.get("status")).lower() == "paid"]
            claimed = round(sum(paid_lines), 2)
            got = _bank_paid(iid, ours, bank, refs or {}, paid_lines)
            if cents(got) != cents(claimed):
                anomalies.append(StatementAnomaly(
                    invoice_id=iid, line=loc, statement_amount=claimed, expected_amount=ours.amount,
                    difference=round(got - claimed, 2), reason="bank_differs", citation=cite,
                    bank_amount=got, retained=retained,
                    note=f"statement says ${claimed:,.2f} paid; the bank shows ${got:,.2f}"))
                flagged = True
        if not flagged:
            matched.append(iid)
    return AnomalyReport(statement_id=statement.id, manager_id=statement.manager_id or "",
                         checked=len(lines), anomalies=anomalies, matched=matched, read_by=read_by)


def detect(statement: Document, ledger: list[LedgerLine], bank: list[dict] | None = None,
           refs: dict[str, list[str]] | None = None) -> AnomalyReport:
    lines, how = read_lines(statement)
    return compare(statement, lines, ledger, how, bank, refs)
