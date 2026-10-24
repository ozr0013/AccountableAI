"""Source-trust rules: when sources disagree about whether a vendor was paid, who wins and why.

Order (highest first):  bank record  >  vendor confirmation / notice  >  manager statement / reply.
  - the manager's bank beats the manager's own statement
  - a vendor confirmation beats a manager "paid" claim
A manager's "paid" claim, however often repeated, is never proof by itself. Absence in a bank
record only counts if that record covers the date of the claim.

`claim` is "paid", "unpaid" or "unknown". Only "paid" closes the question; "unknown" and "unpaid"
both keep an open item (the claim is simply not proven).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel

from contracts.models import Citation, SourceKind
from ..matcher import InvoiceEvidence

_RANK = {
    SourceKind.manager_bank: 3,
    SourceKind.vendor_confirmation: 2,
    SourceKind.vendor_notice: 2,
    SourceKind.manager_statement: 1,
    SourceKind.manager_reply: 1,
}


def trust_rank(kind: SourceKind) -> int:
    return _RANK.get(kind, 0)


class TrustDecision(BaseModel):
    invoice_id: str
    claim: Literal["paid", "unpaid", "unknown"]
    conflict: bool                     # sources disagreed
    winners: list[Citation]
    losers: list[Citation]             # the claims that were overruled
    reason: str


def decide(ev: InvoiceEvidence) -> TrustDecision:
    stmt = ev.cites.get("statement", [])
    manager_claims = stmt + ev.reply_paid
    claimed = bool(ev.statement_paid or ev.reply_paid)
    bank = ev.cites.get("bank", [])
    vendor = ev.cites.get("vendor", [])

    claim_dates = [d for _, d in ev.statement_paid if d]
    since = min(claim_dates) if claim_dates else None
    covering = [c for d, c in ev.bank_docs if since is None or (d and d >= since)]
    bank_silent = bool(covering) and ev.bank_cents == 0

    def out(claim, conflict, winners, losers, reason):
        return TrustDecision(invoice_id=ev.invoice_id, claim=claim, conflict=conflict,
                             winners=winners, losers=losers, reason=reason)

    if ev.bank_cents:
        disputed = ev.vendor_not_received
        return out(
            "paid", disputed, bank + (vendor if disputed else []), [],
            "Manager's bank record shows the payment."
            + (" The vendor's notice says not received; confirm with the vendor." if disputed else ""))

    if ev.vendor_paid:
        return out("paid", False, vendor, [],
                   "Vendor confirmed it was paid."
                   + (" No matching bank payment was found; payment may have gone another way."
                      if bank_silent else ""))

    if ev.vendor_not_received:
        winners = vendor + (covering if bank_silent else [])
        return out("unpaid", claimed, winners, manager_claims,
                   ("Vendor says not received" + (" and the bank record shows no payment" if bank_silent else "")
                    + "; both outrank the manager's statement." if claimed
                    else "Vendor says not received."))

    if claimed and bank_silent:
        return out("unpaid", True, covering, manager_claims,
                   "Manager says paid but the bank record covering that date shows no payment, "
                   "and the vendor has not confirmed.")

    if claimed:
        return out("unknown", False, [], manager_claims,
                   "Manager says paid. No bank record or vendor confirmation backs it, so the "
                   "claim alone is not enough.")

    return out("unpaid", False, covering if bank_silent else [], [],
               "No payment evidence from any source.")
