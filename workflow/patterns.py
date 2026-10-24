"""Manager-level alerts drafted by code: recurring lateness (scenario 4), duplicate billing (8).

A late month = an invoice received that month that Oakline funded and the manager paid after
its due date, or still owes past it (mirrors rails/aging). Recurring = late in at least
RECURRING_MIN_LATE of the last WINDOW months, with at least RECURRING_MIN_MONTHS of history.
"""

from __future__ import annotations

from datetime import date

from contracts.models import (
    AgreementTerms,
    AlertDraft,
    AlertKind,
    Citation,
    Claim,
    LedgerLine,
    SourceKind,
)
from rails.aging.aging import RECURRING_MIN_LATE, RECURRING_MIN_MONTHS

from .book import alert_id

WINDOW = 6


def _late(line: LedgerLine, now: date) -> bool:
    if line.duplicate_of or line.funded_amount <= 0 or not line.due_date:
        return False
    paid_late = line.paid_on is not None and line.paid_on > line.due_date
    owed_late = round(line.paid_amount * 100) < round(line.amount * 100) and now > line.due_date
    return paid_late or owed_late


def history(lines: list[LedgerLine], now: date) -> list[dict]:
    months: dict[str, list[str]] = {}
    for l in lines:
        if l.invoice_received and l.funded_amount > 0 and not l.duplicate_of \
                and l.invoice_received <= now:
            m = months.setdefault(l.invoice_received.strftime("%Y-%m"), [])
            if _late(l, now):
                m.append(l.invoice_id)
    return [{"month": m, "late": bool(ids), "invoice_ids": ids}
            for m, ids in sorted(months.items())[-WINDOW:]]


def pattern_draft(rails, manager_id: str, now: date,
                  terms: AgreementTerms | None) -> AlertDraft | None:
    hist = history(rails.ledger(manager_id), now)
    late = [h for h in hist if h["late"]]
    if len(hist) < RECURRING_MIN_MONTHS or len(late) < RECURRING_MIN_LATE:
        return None
    late_ids = [i for h in late for i in h["invoice_ids"]]
    banks = [Citation(source_id=d.id, kind=d.kind) for d in rails.documents(manager_id)
             if d.kind == SourceKind.manager_bank and (d.dated is None or d.dated <= now)
             and any(p.get("invoice_id") in late_ids for p in d.data.get("payments", []))]
    cites = banks + ([terms.vendor_payment_days.citation] if terms else [])
    months = ", ".join(date.fromisoformat(h["month"] + "-01").strftime("%b %Y") for h in late)
    days = f"{terms.vendor_payment_days.value}-day" if terms else "agreed"
    lines = "; ".join(f"{h['month']}: {'late (' + ', '.join(h['invoice_ids']) + ')' if h['late'] else 'on time'}"
                      for h in hist)
    return AlertDraft(
        id=alert_id(AlertKind.recurring_pattern.value, manager_id, [manager_id]),
        kind=AlertKind.recurring_pattern, manager_id=manager_id, invoice_ids=sorted(late_ids),
        headline=f"{manager_id} paid vendors late in {len(late)} of {len(hist)} months ({months})",
        claims=[Claim(text=f"Month by month, against the {days} payment term: {lines}.",
                      citations=cites)],
        recommended_action="Recommend stricter terms: a shorter payment window, or proof of "
                           "vendor payment with every monthly statement.")


def duplicate_draft(rails, line: LedgerLine) -> AlertDraft | None:
    if not line.duplicate_of:
        return None
    pair = [line.duplicate_of, line.invoice_id]
    cites: dict[str, Citation] = {}
    for d in rails.documents(line.manager_id):
        ids = {d.data.get("invoice_id")} | {x.get("invoice_id") for x in d.data.get("lines", [])}
        if ids & set(pair) and d.kind in (SourceKind.funding, SourceKind.manager_statement,
                                          SourceKind.vendor_invoice):
            cites.setdefault(d.id, Citation(source_id=d.id, kind=d.kind))
    return AlertDraft(
        id=alert_id(AlertKind.duplicate_billing.value, line.manager_id, pair),
        kind=AlertKind.duplicate_billing, manager_id=line.manager_id, invoice_ids=pair,
        amount=line.funded_amount,
        headline=f"{line.invoice_id} repeats {line.duplicate_of} (same vendor invoice); "
                 f"${line.funded_amount:,.2f} funded twice",
        claims=[Claim(text=f"{line.invoice_id} bills the same vendor invoice as "
                           f"{line.duplicate_of}, and Oakline funded both.",
                      citations=list(cites.values()))],
        recommended_action=f"Ask the manager to return or offset the duplicate "
                           f"${line.funded_amount:,.2f}.")
