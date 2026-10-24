"""Aging and per-manager lateness patterns.

An invoice counts against the manager only when the manager is the reason it is late:
funded, past its due date from confirmed terms, and either paid after the due date or still
owed after authorized holds. Unfunded invoices (owner-side) and held holdback never count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from contracts.models import Event

from ..ledger import build_ledger
from ..recon import breakdown

BUCKETS = ("current", "1-30", "31-60", "61-90", "90+")
RECURRING_MIN_LATE = 3        # late in at least this many months...
RECURRING_MIN_MONTHS = 4      # ...out of at least this many months of history


def _bucket(days_past_due: int) -> str:
    if days_past_due <= 0:
        return "current"
    for hi, name in ((30, "1-30"), (60, "31-60"), (90, "61-90")):
        if days_past_due <= hi:
            return name
    return "90+"


def aging(events: list[Event], manager_id: str, as_of: date) -> dict[str, float]:
    """Vendor money still owed by the manager, bucketed by days past the due date."""
    due = {l.invoice_id: l.due_date for l in build_ledger(events) if l.manager_id == manager_id}
    out = {b: 0 for b in BUCKETS}
    for r in breakdown(events, manager_id):
        if r.owed and due.get(r.invoice_id):
            out[_bucket((as_of - due[r.invoice_id]).days)] += r.owed
    return {b: v / 100 for b, v in out.items()}


def _late_invoices(events: list[Event], manager_id: str, as_of: date) -> dict[str, list[str]]:
    """month (of invoice receipt) -> ids of invoices late because of the manager."""
    owed = {r.invoice_id: r.owed for r in breakdown(events, manager_id)}
    months: dict[str, list[str]] = {}
    for l in build_ledger(events):
        if l.manager_id != manager_id or not l.invoice_received or l.duplicate_of:
            continue
        if l.funded_amount <= 0:
            continue
        m = months.setdefault(l.invoice_received.strftime("%Y-%m"), [])
        if not l.due_date:
            continue
        paid_late = l.paid_on is not None and l.paid_on > l.due_date
        unpaid_late = owed.get(l.invoice_id, 0) > 0 and as_of > l.due_date
        if paid_late or unpaid_late:
            m.append(l.invoice_id)
    return months


def late_months(events: list[Event], manager_id: str, as_of: date) -> dict[str, bool]:
    """Contract shape: "2026-03" -> was the manager late that month?"""
    return {m: bool(ids) for m, ids in sorted(_late_invoices(events, manager_id, as_of).items())}


@dataclass
class Pattern:
    manager_id: str
    months: int
    late: int
    recurring: bool
    history: list[dict] = field(default_factory=list)   # [{month, late, invoice_ids}]


def pattern(events: list[Event], manager_id: str, as_of: date, window: int = 6) -> Pattern:
    """Recurring lateness over the last `window` months, with the history to cite."""
    by_month = sorted(_late_invoices(events, manager_id, as_of).items())[-window:]
    late = sum(1 for _, ids in by_month if ids)
    return Pattern(
        manager_id=manager_id, months=len(by_month), late=late,
        recurring=len(by_month) >= RECURRING_MIN_MONTHS and late >= RECURRING_MIN_LATE,
        history=[{"month": m, "late": bool(ids), "invoice_ids": ids} for m, ids in by_month])
