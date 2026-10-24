"""The next moment something is due, so "advance time" always lands on something that happens."""

from __future__ import annotations

from datetime import date, timedelta

from ..guards.templates import LEVEL_ORDER, MIN_DAYS_BETWEEN


def next_deadline(rails, book, today: date) -> tuple[date, str] | None:
    """Earliest future date when a reminder becomes due or an unpaid invoice becomes overdue."""
    options: list[tuple[date, str]] = []
    for manager, sent in book.chases.items():
        last = sent[-1]
        if last.sent_at and last.level != LEVEL_ORDER[-1]:
            nxt = LEVEL_ORDER[LEVEL_ORDER.index(last.level) + 1]
            when = last.sent_at.date() + timedelta(days=MIN_DAYS_BETWEEN)
            options.append((when, f"{nxt} reminder to {manager} is due"))
    for manager in {a.draft.manager_id for a in book.open_alerts()}:
        for line in rails.ledger(manager):
            if (not line.duplicate_of and line.funded_amount > 0 and line.due_date
                    and round(line.paid_amount * 100) < round(line.amount * 100)):
                options.append((line.due_date + timedelta(days=1),
                                f"{line.invoice_id} becomes overdue"))
    future = [o for o in options if o[0] > today]
    return min(future) if future else None
