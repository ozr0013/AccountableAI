"""Funding ledger: per vendor invoice, what Oakline funded and what the vendor actually got.

Derived purely from events, so it can be rebuilt by replay. Amounts are summed in integer
cents and converted to float only at the LedgerLine boundary.

Evidence for "paid to the vendor" follows the source-trust order (see rails/trust):
the manager's bank records and the vendor's confirmation count; the manager's own
statement saying "paid" does not.
"""

from __future__ import annotations

from datetime import date, timedelta

from contracts.models import Document, Event, InvoiceStatus, LedgerLine, SourceKind

from ..docs import documents_from_events
from ..facts import terms_at
from ..matcher import find_duplicates, gather_evidence


def cents(x: float) -> int:
    return round(x * 100)


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def build_ledger(events: list[Event]) -> list[LedgerLine]:
    """Pass current events (superseded ones already dropped). Returns lines sorted by invoice id."""


    from ..guards.payments import identified_payment_documents
    docs = identified_payment_documents(documents_from_events(events))
    result = gather_evidence(docs)
    dups = find_duplicates(docs)
    funded: dict[str, list[tuple[int, date | None]]] = {}
    inv: dict[str, Document] = {}
    for d in docs:
        iid = d.data.get("invoice_id")
        if d.kind == SourceKind.vendor_invoice and iid:
            inv[iid] = d
        elif d.kind == SourceKind.funding and iid:
            funded.setdefault(iid, []).append((cents(d.data["amount"]), _d(d.data.get("funded_on"))))

    lines = []
    for iid in sorted(inv):
        d, ev = inv[iid], result.evidence[iid]
        amount_c, received = cents(d.data["amount"]), _d(d.data.get("received"))
        f = funded.get(iid, [])
        funded_c = sum(c for c, _ in f)
        if ev.bank_cents:                       # bank beats vendor confirmation when both exist
            paid_c, paid_on = ev.bank_cents, max(ev.bank_on, default=None)
        elif ev.vendor_paid:
            paid_c, paid_on = ev.vendor_paid
        else:
            paid_c, paid_on = 0, None
        terms = terms_at(events, d.manager_id, received, d.vendor_id) or {}
        days = terms.get("vendor_payment_days")
        due = received
        if days is None or received is None:
            due = None
        elif terms.get("deadline_basis", "calendar") == "business":
            remaining = int(days)
            while remaining > 0:
                due += timedelta(days=1)
                if due.weekday() < 5:
                    remaining -= 1
        else:
            due += timedelta(days=int(days))
        if paid_c >= amount_c > 0:
            status = InvoiceStatus.paid
        elif paid_c > 0:
            status = InvoiceStatus.partially_paid
        elif funded_c > 0:
            status = InvoiceStatus.funded
        else:
            status = InvoiceStatus.unfunded
        lines.append(LedgerLine(
            invoice_id=iid, manager_id=d.manager_id, vendor_id=d.vendor_id,
            amount=amount_c / 100, invoice_received=received, funded_amount=funded_c / 100,
            funded_on=min((x for _, x in f if x), default=None),
            paid_amount=paid_c / 100, paid_on=paid_on if paid_c else None,
            due_date=due, status=status, duplicate_of=dups.get(iid)))
    return lines
