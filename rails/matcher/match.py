"""Matcher: line up vendor payments across the manager's bank records, the manager's
statements and the vendors' own confirmations, and flag duplicate billing.

Exact invoice-id match first. A bank payment with no invoice id falls back to a unique
(manager, vendor, exact amount, date window) match. Anything still ambiguous goes to the
optional `fuzzy` hook (a Haiku call later) or stays unmatched; we never guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from contracts.models import Citation, Document, SourceKind

WINDOW_DAYS = 30

Payment = dict  # {"invoice_id"?, "vendor_id", "amount", "on"}
Fuzzy = Callable[[Payment, list[str]], "str | None"]


def cents(x: float) -> int:
    return round(x * 100)


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


@dataclass
class InvoiceEvidence:
    invoice_id: str
    bank_cents: int = 0
    bank_on: list[date] = field(default_factory=list)
    statement_paid: list[tuple[int, date | None]] = field(default_factory=list)  # manager's claim
    vendor_paid: tuple[int, date | None] | None = None                          # vendor confirmed
    vendor_not_received: bool = False                                           # past-due notice
    reply_paid: list[Citation] = field(default_factory=list)                    # manager reply "paid"
    bank_docs: list[tuple[date | None, Citation]] = field(default_factory=list)  # bank records seen
    cites: dict[str, list[Citation]] = field(default_factory=dict)  # bank|statement|vendor


@dataclass
class MatchResult:
    evidence: dict[str, InvoiceEvidence]
    unmatched_payments: list[tuple[str, Payment]]   # (doc id, payment) tied to no invoice


def _cite(doc: Document, locator: str | None = None) -> Citation:
    return Citation(source_id=doc.id, kind=doc.kind, locator=locator)


def gather_evidence(docs: list[Document], fuzzy: Fuzzy | None = None) -> MatchResult:
    invoices = {d.data["invoice_id"]: d for d in docs
                if d.kind == SourceKind.vendor_invoice and d.data.get("invoice_id")}
    ev = {iid: InvoiceEvidence(iid) for iid in invoices}
    unmatched: list[tuple[str, Payment]] = []

    def get(iid: str) -> InvoiceEvidence | None:
        return ev.get(iid)

    for d in docs:
        data = d.data
        if d.kind == SourceKind.manager_bank:
            for p in data.get("payments", []):
                iid = p.get("invoice_id") or _fallback(p, d, invoices, ev, fuzzy)
                e = get(iid) if iid else None
                if e is None:
                    unmatched.append((d.id, p))
                    continue
                e.bank_cents += cents(p["amount"])
                if _d(p.get("on")):
                    e.bank_on.append(_d(p["on"]))
                e.cites.setdefault("bank", []).append(_cite(d))
        elif d.kind == SourceKind.manager_statement:
            for ln in data.get("lines", []):
                e = get(ln.get("invoice_id"))
                if e and ln.get("status") == "paid":
                    e.statement_paid.append((cents(ln["amount"]), _d(ln.get("on"))))
                    e.cites.setdefault("statement", []).append(_cite(d, ln.get("line")))
        elif d.kind == SourceKind.vendor_confirmation:
            e = get(data.get("invoice_id"))
            if e and data.get("status") == "paid":
                e.vendor_paid = (cents(data["amount"]), _d(data.get("paid_on")))
                e.cites.setdefault("vendor", []).append(_cite(d))
        elif d.kind == SourceKind.vendor_notice:
            e = get(data.get("invoice_id"))
            if e and data.get("status") == "not_received":
                e.vendor_not_received = True
                e.cites.setdefault("vendor", []).append(_cite(d))
        elif d.kind == SourceKind.manager_reply:
            e = get(data.get("invoice_id"))
            if e and data.get("status") == "paid":
                e.reply_paid.append(_cite(d))
    for d in docs:   # which bank records could have shown a payment for each invoice
        if d.kind == SourceKind.manager_bank:
            for iid, inv_doc in invoices.items():
                if inv_doc.manager_id == d.manager_id:
                    ev[iid].bank_docs.append((d.dated, _cite(d)))
    return MatchResult(ev, unmatched)


def _fallback(p: Payment, bank: Document, invoices: dict[str, Document],
              ev: dict[str, InvoiceEvidence], fuzzy: Fuzzy | None) -> str | None:
    """Unique exact-amount match inside the date window, else the fuzzy hook, else None."""
    on = _d(p.get("on"))
    cands = []
    for iid, inv in invoices.items():
        if inv.manager_id != bank.manager_id or inv.vendor_id != p.get("vendor_id"):
            continue
        if ev[iid].bank_cents or cents(inv.data["amount"]) != cents(p["amount"]):
            continue
        rec = _d(inv.data.get("received"))
        if on and rec and not (rec <= on <= rec + timedelta(days=WINDOW_DAYS)):
            continue
        cands.append(iid)
    if len(cands) == 1:
        return cands[0]
    return fuzzy(p, cands) if fuzzy and cands else None


def match(docs: list[Document], fuzzy: Fuzzy | None = None) -> MatchResult:
    return gather_evidence(docs, fuzzy)


# --- duplicate billing (scenario 8) ----------------------------------------------------------

_REF = re.compile(r"Invoice\s+([A-Za-z0-9-]+)")


def vendor_ref(doc: Document) -> str | None:
    return doc.data.get("vendor_ref") or (m.group(1) if (m := _REF.search(doc.text)) else None)


def find_duplicates(docs: list[Document]) -> dict[str, str]:
    """duplicate invoice id -> original invoice id (the earlier one stays the original).

    Same vendor resubmitting the same vendor invoice number under another Oakline invoice id,
    or identical vendor + amount + service period when the invoice states one. A recurring
    monthly charge with a different period is not a duplicate.
    """
    invs = sorted(
        (d for d in docs if d.kind == SourceKind.vendor_invoice and d.data.get("invoice_id")),
        key=lambda d: (d.data.get("received") or "", d.data["invoice_id"]))
    seen_ref: dict[tuple, str] = {}
    seen_period: dict[tuple, str] = {}
    dups: dict[str, str] = {}
    for d in invs:
        iid = d.data["invoice_id"]
        keys = []
        if ref := vendor_ref(d):
            keys.append((seen_ref, (d.manager_id, d.vendor_id, ref)))
        if period := d.data.get("service_period"):
            keys.append((seen_period, (d.manager_id, d.vendor_id, cents(d.data["amount"]), period)))
        for table, k in keys:
            if k in table and table[k] != iid:
                dups.setdefault(iid, table[k])
            else:
                table.setdefault(k, iid)
    return dups
