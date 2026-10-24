"""Payment trail by rules: one timeline per invoice, winner picked by rails source-trust rules."""

from __future__ import annotations

from datetime import date

from contracts.models import Citation, Document, LedgerLine, PaymentTrail, SourceKind, TrailEntry
from rails.matcher import gather_evidence
from rails.trust import TrustDecision, decide


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _entry(d: Document, claim: str, on: date | None = None, locator: str | None = None) -> TrailEntry:
    return TrailEntry(on=on or d.dated, kind=d.kind, claim=claim,
                      citation=Citation(source_id=d.id, kind=d.kind, locator=locator))


def trust_decision(line: LedgerLine, docs: list[Document]) -> TrustDecision | None:
    ev = gather_evidence(docs).evidence.get(line.invoice_id)
    return decide(ev) if ev else None


def timeline(line: LedgerLine, docs: list[Document], dec: TrustDecision | None) -> list[TrailEntry]:
    """Only documents that say something about this invoice (plus bank records the trust rules
    used as 'no payment found')."""
    iid = line.invoice_id
    used = {c.source_id for c in (dec.winners + dec.losers)} if dec else set()
    out: list[TrailEntry] = []
    for d in docs:
        data = d.data
        if d.kind == SourceKind.vendor_invoice and data.get("invoice_id") == iid:
            out.append(_entry(d, f"invoiced {_money(data['amount'])}, received "
                                 f"{data.get('received', d.dated)}", _d(data.get("received"))))
        elif d.kind == SourceKind.funding and data.get("invoice_id") == iid:
            out.append(_entry(d, f"Oakline funded {_money(data['amount'])}", _d(data.get("funded_on"))))
        elif d.kind == SourceKind.manager_statement:
            for ln in data.get("lines", []):
                if ln.get("invoice_id") == iid:
                    out.append(_entry(d, f"manager statement: {ln.get('status', '?')}",
                                      _d(ln.get("on")) or d.dated, ln.get("line")))
        elif d.kind == SourceKind.manager_bank:
            pays = [p for p in data.get("payments", []) if p.get("invoice_id") == iid]
            for p in pays:
                out.append(_entry(d, f"bank: paid {_money(p['amount'])}", _d(p.get("on"))))
            if not pays and d.id in used:
                out.append(_entry(d, "bank: no payment found"))
        elif d.kind == SourceKind.vendor_confirmation and data.get("invoice_id") == iid:
            out.append(_entry(d, "vendor confirms paid", _d(data.get("paid_on"))))
        elif d.kind == SourceKind.vendor_notice and data.get("invoice_id") == iid:
            out.append(_entry(d, f"vendor: {data.get('status', 'notice').replace('_', ' ')}"))
        elif d.kind == SourceKind.manager_reply and data.get("invoice_id") == iid:
            out.append(_entry(d, "manager reply says paid, no proof attached"
                              if data.get("status") == "paid" else "manager reply"))
    return sorted(out, key=lambda e: (str(e.on or ""), e.citation.source_id))


def build_trail(line: LedgerLine, docs: list[Document]) -> PaymentTrail:
    dec = trust_decision(line, docs)
    entries = timeline(line, docs, dec)
    if dec is None:
        return PaymentTrail(invoice_id=line.invoice_id, entries=entries, conflict=False,
                            winning_claim="unknown", winning_sources=[],
                            reasoning="No vendor invoice on file for this item.")
    return PaymentTrail(invoice_id=line.invoice_id, entries=entries, conflict=dec.conflict,
                        winning_claim=dec.claim, winning_sources=dec.winners,
                        reasoning=dec.reason)
