"""Funding ledger: per vendor invoice, what Oakline funded and what the vendor actually got.

Derived purely from events, so it can be rebuilt by replay. Amounts are summed in integer
cents and converted to float only at the LedgerLine boundary.

Evidence for "paid to the vendor" follows the source-trust order (see rails/trust):
the manager's bank records and the vendor's confirmation count; the manager's own
statement saying "paid" does not.
"""

from __future__ import annotations

from datetime import date, timedelta

from contracts.models import Event, InvoiceStatus, LedgerLine


def cents(x: float) -> int:
    return round(x * 100)


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def build_ledger(events: list[Event]) -> list[LedgerLine]:
    """Pass current events (superseded ones already dropped). Returns lines sorted by invoice id."""
    terms: dict[str, int] = {}
    inv: dict[str, dict] = {}
    bank: dict[str, list[tuple[int, date | None]]] = {}
    conf: dict[str, tuple[int, date | None]] = {}
    funded: dict[str, list[tuple[int, date | None]]] = {}

    for e in events:
        if e.type == "terms_confirmed" and e.manager_id:
            terms[e.manager_id] = int(e.payload["vendor_payment_days"])
        if e.type != "document_ingested":
            continue
        kind, data = e.payload.get("kind"), e.payload.get("data", {})
        if kind == "vendor_invoice" and data.get("invoice_id"):
            inv[data["invoice_id"]] = {
                "manager_id": e.payload.get("manager_id"), "vendor_id": e.payload.get("vendor_id"),
                "amount": cents(data["amount"]), "received": _d(data.get("received")),
            }
        elif kind == "funding" and data.get("invoice_id"):
            funded.setdefault(data["invoice_id"], []).append(
                (cents(data["amount"]), _d(data.get("funded_on"))))
        elif kind == "manager_bank":
            for p in data.get("payments", []):
                bank.setdefault(p["invoice_id"], []).append((cents(p["amount"]), _d(p.get("on"))))
        elif kind == "vendor_confirmation" and data.get("status") == "paid":
            conf[data["invoice_id"]] = (cents(data["amount"]), _d(data.get("paid_on")))

    lines = []
    for iid in sorted(inv):
        i = inv[iid]
        f = funded.get(iid, [])
        funded_c = sum(c for c, _ in f)
        if iid in bank:
            paid_c = sum(c for c, _ in bank[iid])
            paid_on = max((d for _, d in bank[iid] if d), default=None)
        elif iid in conf:
            paid_c, paid_on = conf[iid]
        else:
            paid_c, paid_on = 0, None
        days = terms.get(i["manager_id"])
        due = i["received"] + timedelta(days=days) if days and i["received"] else None
        if paid_c >= i["amount"] > 0:
            status = InvoiceStatus.paid
        elif paid_c > 0:
            status = InvoiceStatus.partially_paid
        elif funded_c > 0:
            status = InvoiceStatus.funded
        else:
            status = InvoiceStatus.unfunded
        lines.append(LedgerLine(
            invoice_id=iid, manager_id=i["manager_id"], vendor_id=i["vendor_id"],
            amount=i["amount"] / 100, invoice_received=i["received"],
            funded_amount=funded_c / 100,
            funded_on=min((d for _, d in f if d), default=None),
            paid_amount=paid_c / 100, paid_on=paid_on if paid_c else None,
            due_date=due, status=status))
    return lines
