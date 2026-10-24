"""Delay cause by rules: exactly one of five causes per delayed invoice, with citations.

Order matters (first match wins):
  1. owner_side         Oakline never funded it (e.g. over the approval limit)
  2. vendor_side        paid on or before the due date under the agreement terms
  3. authorized_hold    only the agreement's holdback is unpaid and inspection is pending
  4. paperwork_pending  the manager's records say it's held for missing vendor paperwork
  5. unexplained        funded, past due, no reason on file
"""

from __future__ import annotations

import re

from contracts.models import (
    AgreementTerms,
    Cause,
    CauseFinding,
    Citation,
    LedgerLine,
    PaymentTrail,
    SourceKind,
)

PAPERWORK = re.compile(r"w-?9|paperwork|missing|incomplete|not on file", re.IGNORECASE)
PAYMENT_KINDS = {SourceKind.manager_statement, SourceKind.manager_bank,
                 SourceKind.vendor_confirmation, SourceKind.vendor_notice, SourceKind.manager_reply}


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _cents(x: float) -> int:
    return round(x * 100)


def _cites(trail: PaymentTrail, *kinds: SourceKind) -> list[Citation]:
    seen: dict[str, Citation] = {}
    for e in trail.entries:
        if e.kind in kinds:
            seen.setdefault(e.citation.source_id, e.citation)
    return list(seen.values())


def holdback_room(line: LedgerLine, terms: AgreementTerms) -> float:
    pct = float(terms.holdback_pct.value) if terms.holdback_pct else 0.0
    return round(line.amount * pct / 100, 2)


def inspection_pending(trail: PaymentTrail) -> bool:
    return any(e.kind == SourceKind.vendor_invoice and "inspection pending" in e.claim
               for e in trail.entries)


def paperwork_hold(trail: PaymentTrail) -> bool:
    return any(e.kind == SourceKind.manager_statement and "hold" in e.claim.lower()
               and PAPERWORK.search(e.claim) for e in trail.entries)


def hard_facts(line: LedgerLine, trail: PaymentTrail, terms: AgreementTerms) -> dict:
    """The facts no answer may contradict (also shown to the LLM)."""
    unpaid = round(line.amount - line.paid_amount, 2)
    return {
        "funded": line.funded_amount > 0,
        "paid_on_time": bool(line.paid_on and line.due_date and line.paid_on <= line.due_date
                             and _cents(line.paid_amount) >= _cents(line.amount)),
        "unpaid_amount": unpaid,
        "within_holdback": 0 < _cents(unpaid) <= _cents(holdback_room(line, terms)),
        "inspection_pending": inspection_pending(trail),
        "paperwork_hold": paperwork_hold(trail),
        "trust_rules_winner": trail.winning_claim,
        "bank_change_pending": any(e.kind == SourceKind.vendor_notice and "bank change" in e.claim
                                   for e in trail.entries),
    }


def classify(line: LedgerLine, trail: PaymentTrail, terms: AgreementTerms) -> CauseFinding:
    f = hard_facts(line, trail, terms)
    days = terms.vendor_payment_days
    funding = _cites(trail, SourceKind.funding)
    invoice = _cites(trail, SourceKind.vendor_invoice)
    payment = _cites(trail, *PAYMENT_KINDS)

    def out(cause: Cause, conf: float, why: str, cites: list[Citation]) -> CauseFinding:
        uniq = list({c.source_id: c for c in cites}.values())
        return CauseFinding(invoice_id=line.invoice_id, cause=cause, confidence=conf,
                            explanation=why, citations=uniq)

    if not f["funded"]:
        limit = terms.approval_limit
        over = limit and line.amount > float(limit.value)
        why = (f"Oakline has not funded this {_money(line.amount)} invoice"
               + (f"; it is over the {_money(float(limit.value))} approval limit "
                  f"(clause {limit.citation.locator}), so it waits on Oakline's approval." if over
                  else "; the manager has no Oakline money to pay it with."))
        return out(Cause.owner_side, 0.95, why, invoice + ([limit.citation] if over else []))

    if f["paid_on_time"]:
        return out(Cause.vendor_side, 0.9,
                   f"Paid {line.paid_on} against a due date of {line.due_date} "
                   f"({days.value} days from receipt, clause {days.citation.locator}): on time "
                   "under the agreement.", invoice + payment + [days.citation])

    change = [e.citation for e in trail.entries
              if e.kind == SourceKind.vendor_notice and "bank change" in e.claim]
    if change:
        return out(Cause.vendor_side, 0.9,
                   "The vendor asked to change its bank details; payment is held until the "
                   "request is verified and a controller approves it.", change + funding)

    if f["within_holdback"] and f["inspection_pending"] and terms.holdback_pct:
        hb = terms.holdback_pct
        return out(Cause.authorized_hold, 0.9,
                   f"{_money(line.paid_amount)} paid; the remaining {_money(f['unpaid_amount'])} "
                   f"is within the {hb.value}% holdback the agreement allows until inspection "
                   f"passes (clause {hb.citation.locator}).", funding + payment + [hb.citation])

    if paperwork_hold(trail):
        return out(Cause.paperwork_pending, 0.85,
                   "The manager's statement shows the payment on hold for missing vendor "
                   "paperwork; a document request to the vendor is the next step.",
                   funding + _cites(trail, SourceKind.manager_statement))

    late = f"due {line.due_date}" if line.due_date else "past due"
    return out(Cause.unexplained, 0.8,
               f"Oakline funded {_money(line.funded_amount)}; {_money(f['unpaid_amount'])} "
               f"is still unpaid ({late}, {days.value} days from receipt under clause "
               f"{days.citation.locator}). {trail.reasoning} No authorized hold, paperwork "
               "issue or owner-side reason is on file.", funding + payment + [days.citation])
