"""Funded-vs-paid reconciliation per manager.

    funded = paid + authorized holds + still owed          (diff = the part that doesn't fit)

- paid:   what bank / vendor evidence shows reached the vendor (see rails/ledger)
- held:   holdback the agreement allows while an inspection is pending
- owed:   funded vendor money the manager still has: min(invoice, funded) - paid - held
- diff:   never silently absorbed. Over-funding, funded duplicate invoices, or the manager
          paying more than was funded all show up here.

Extra keys (additive): `unfunded` = invoice money Oakline never funded (owner-side, scenario 6)
and `duplicate_funded` = funded amount on invoices flagged as duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass

from contracts.models import Event, SourceKind

from ..docs import documents_from_events
from ..facts import terms_at
from ..ledger import build_ledger


def _c(x: float) -> int:
    return round(x * 100)


@dataclass
class ReconLine:
    invoice_id: str
    manager_id: str
    funded: int
    paid: int
    held: int
    owed: int
    unfunded: int
    duplicate_funded: int

    @property
    def diff(self) -> int:
        return self.funded - (self.paid + self.held + self.owed)


def breakdown(events: list[Event], manager_id: str | None = None) -> list[ReconLine]:
    from ..guards.payments import payable_cents
    invoices = {d.data["invoice_id"]: d.data for d in documents_from_events(events)
                if d.kind == SourceKind.vendor_invoice and d.data.get("invoice_id")}
    out = []
    for l in build_ledger(events):
        if manager_id and l.manager_id != manager_id:
            continue
        amount, funded, paid = _c(l.amount), _c(l.funded_amount), _c(l.paid_amount)
        if l.duplicate_of:
            out.append(ReconLine(l.invoice_id, l.manager_id, funded, paid, 0, 0, 0, funded))
            continue
        terms = terms_at(events, l.manager_id, l.invoice_received, l.vendor_id) or {}
        permitted = amount - payable_cents(l.amount, terms, invoices.get(l.invoice_id, {}))
        held = min(max(funded - paid, 0), permitted)
        owed = max(min(amount, funded) - paid - held, 0)
        out.append(ReconLine(l.invoice_id, l.manager_id, funded, paid, held, owed,
                             max(amount - funded, 0) if paid < amount else 0, 0))
    return out


def reconcile(events: list[Event], manager_id: str) -> dict[str, float]:
    """Contract shape: funded, paid, held, owed, diff (+ unfunded, duplicate_funded)."""
    lines = breakdown(events, manager_id)
    tot = lambda f: sum(getattr(l, f) for l in lines)
    res = {k: tot(k) / 100 for k in ("funded", "paid", "held", "owed", "unfunded", "duplicate_funded")}
    res["diff"] = sum(l.diff for l in lines) / 100
    return res
