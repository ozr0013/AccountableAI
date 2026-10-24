"""Cross-document reasoning a payables clerk does by hand: drop duplicate invoices, find the
Keyway reference an invoice forgot, tie each bank line and each "we got paid" email to an
invoice, split combined payments. Anything ambiguous is flagged for a person, never guessed."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from itertools import combinations

from contracts.models import SourceKind

from .parse import Parsed


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def _eq(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) < 0.005


def link(parsed: list[Parsed]) -> list[Parsed]:
    kinds = defaultdict(list)
    for p in parsed:
        if p.kind is not None and not (p.kind == SourceKind.vendor_invoice and p.method == "none"):
            kinds[p.kind].append(p)
    invoices = kinds[SourceKind.vendor_invoice]
    funding = kinds[SourceKind.funding]

    # 1. Duplicate copies of the same invoice (forwarded twice): keep the first one received.
    seen: dict[tuple, Parsed] = {}
    for p in sorted(invoices, key=lambda p: (p.dated or date.max, p.source)):
        key = (p.vendor_id, p.meta.get("vendor_no"), p.data.get("amount"), p.data.get("invoice_id"))
        if p.meta.get("vendor_no") and key in seen:
            p.meta["duplicate_of"] = seen[key].doc_id
            p.flag(f"duplicate copy of {seen[key].source}; not counted twice")
            p.kind = None
        else:
            seen[key] = p
    invoices = [p for p in invoices if p.kind is not None]

    # 2. Invoices with no Keyway reference: the wire remittance names vendor + amount.
    claimed = {p.data["invoice_id"] for p in invoices if p.data.get("invoice_id")}
    for p in invoices:
        if not p.meta.get("needs_invoice_id"):
            continue
        cands = [f for f in funding if f.vendor_id == p.vendor_id
                 and _eq(f.data.get("amount"), p.data.get("amount"))
                 and f.data["invoice_id"] not in claimed]
        if len(cands) == 1:
            p.data["invoice_id"] = cands[0].data["invoice_id"]
            claimed.add(p.data["invoice_id"])
            p.flag(f"no Keyway reference on the invoice; matched to wire line "
                   f"{cands[0].data['invoice_id']} by vendor and amount", conf=0.85)
        else:
            p.flag("no Keyway reference and no unique wire line to match", human=True, conf=0.3)
            p.kind = None
    invoices = [p for p in invoices if p.kind is not None]

    # 2b. The vendor's invoice against Oakline's own wire for it. Disagreement is never settled
    #     by picking one: it goes to a person (vendor overbilled, OCR misread, or underfunded).
    funded = {f.data["invoice_id"]: f.data.get("amount") for f in funding}
    for p in invoices:
        f_amt = funded.get(p.data.get("invoice_id"))
        if f_amt is not None and not _eq(f_amt, p.data.get("amount")):
            p.flag(f"invoice says {p.data.get('amount') or 0:,.2f} but Oakline funded "
                   f"{f_amt:,.2f} for {p.data['invoice_id']}", human=True, conf=0.4)
    by_id = {p.data["invoice_id"]: p for p in invoices}
    by_vendor = defaultdict(list)
    for p in invoices:
        by_vendor[p.vendor_id].append(p)

    # 3. Statement lines that cite the vendor's own invoice number (GE-2204).
    for st in kinds[SourceKind.manager_statement]:
        for ln in st.data.get("lines", []):
            vref = ln.pop("_vendor_ref", None)
            vid = ln.pop("_vendor_id", None)
            if ln.get("invoice_id"):
                continue
            cands = [p for p in invoices if vref and p.meta.get("vendor_no") == vref]
            if not cands and vid:
                cands = [p for p in by_vendor[vid] if _eq(p.data.get("amount"), ln["amount"])]
            asof = st.dated or date.max
            cands = sorted([p for p in cands if (p.dated or date.min) <= asof],
                           key=lambda p: p.dated or date.min)
            if cands:
                ln["invoice_id"] = cands[-1].data["invoice_id"]
                if len(cands) > 1:
                    st.flag(f"{ln['line']}: vendor invoice {vref} matches {len(cands)} Keyway "
                            f"invoices; used the latest before the statement date")
            else:
                st.flag(f"{ln['line']}: can't tell which invoice '{vref}' is", human=True, conf=0.5)
        st.data["lines"] = [ln for ln in st.data.get("lines", []) if ln.get("invoice_id")]

    # 4. Bank lines -> invoices. Reference in the memo first, then vendor + amount, then a
    #    combined payment that adds up to exactly one set of the vendor's open invoices.
    paid: set[str] = set()
    for bank in kinds[SourceKind.manager_bank]:
        out = []
        pending = []
        for pay in bank.data.get("payments", []):
            refs = pay.pop("_refs", [])
            desc = pay.pop("_desc", "")
            retain = pay.pop("_retainage", False)
            if len(refs) == 1:
                out.append(dict(pay, invoice_id=refs[0]))
                paid.add(refs[0])
                if retain:
                    bank.flag(f"{refs[0]} paid short ({pay['amount']:,.2f}): memo says retainage held",
                              conf=0.95)
            elif len(refs) > 1 and all(r in by_id for r in refs) and \
                    _eq(sum(by_id[r].data["amount"] for r in refs), pay["amount"]):
                for r in refs:
                    out.append(dict(pay, invoice_id=r, amount=by_id[r].data["amount"]))
                    paid.add(r)
                bank.flag(f"one {pay['amount']:,.2f} payment covers {', '.join(refs)}", conf=0.95)
            else:
                pending.append((pay, desc))
        for pay, desc in pending:
            on = _d(pay["on"]) or date.max
            opens = [p for p in by_vendor.get(pay["vendor_id"], [])
                     if p.data["invoice_id"] not in paid and (p.dated or date.min) <= on + timedelta(days=3)]
            single = [p for p in opens if _eq(p.data["amount"], pay["amount"])]
            if not single:   # the invoice amount may be the disputed one: try what Oakline funded
                single = [p for p in opens if _eq(funded.get(p.data["invoice_id"]), pay["amount"])]
            if len(single) == 1:
                inv = single[0].data["invoice_id"]
                out.append(dict(pay, invoice_id=inv))
                paid.add(inv)
                bank.flag(f"'{desc}' has no reference; matched to {inv} by vendor and amount",
                          conf=0.85)
                continue
            held = [p for p in opens if _eq(round(p.data["amount"] * 0.9, 2), pay["amount"])]
            if not single and len(held) == 1:
                inv = held[0].data["invoice_id"]
                out.append(dict(pay, invoice_id=inv))
                paid.add(inv)
                bank.flag(f"'{desc}' pays 90% of {inv}: consistent with a 10% holdback",
                          conf=0.8)
                continue
            combos = [c for n in (2, 3) for c in combinations(opens, n)
                      if _eq(sum(p.data["amount"] for p in c), pay["amount"])] if not single else []
            if len(combos) == 1:
                for p in combos[0]:
                    out.append(dict(pay, invoice_id=p.data["invoice_id"], amount=p.data["amount"]))
                    paid.add(p.data["invoice_id"])
                bank.flag(f"'{desc}' ({pay['amount']:,.2f}) is a combined payment for "
                          f"{', '.join(p.data['invoice_id'] for p in combos[0])}", conf=0.75)
            else:
                bank.flag(f"can't tie '{desc}' ({pay['amount']:,.2f}) to one invoice "
                          f"({len(single) or len(combos)} candidates)", human=True, conf=0.5)
        bank.data["payments"] = out

    # 5. "We got paid" emails and past-due notices that only give an amount.
    for p in kinds[SourceKind.vendor_confirmation] + kinds[SourceKind.vendor_notice]:
        if not p.meta.get("needs_invoice_id"):
            continue
        amt = p.data.get("amount", p.meta.get("amount"))
        cands = [i for i in by_vendor.get(p.vendor_id, []) if _eq(i.data["amount"], amt)
                 and (i.dated or date.min) <= (p.dated or date.max)]
        if len(cands) > 1:
            in_bank = [i for i in cands if i.data["invoice_id"] in paid]
            cands = in_bank if p.kind == SourceKind.vendor_confirmation and len(in_bank) == 1 else cands
        if len(cands) == 1:
            p.data["invoice_id"] = cands[0].data["invoice_id"]
            p.flag(f"no invoice reference; matched to {p.data['invoice_id']} by vendor and amount",
                   conf=0.85)
        elif not cands and p.kind == SourceKind.vendor_confirmation and amt is not None:
            pool = [i for i in by_vendor.get(p.vendor_id, []) if (i.dated or date.min) <= (p.dated or date.max)]
            combos = [c for n in (2, 3) for c in combinations(pool, n)
                      if _eq(sum(i.data["amount"] for i in c), amt)]
            paid_combos = [c for c in combos if all(i.data["invoice_id"] in paid for i in c)]
            combos = paid_combos if len(paid_combos) == 1 else combos
            if len(combos) == 1:
                ids = [i.data["invoice_id"] for i in combos[0]]
                p.data.update(invoice_id=ids[0], amount=combos[0][0].data["amount"])
                p.part = ids[0]
                p.flag(f"one {amt:,.2f} confirmation covers {', '.join(ids)}", conf=0.75)
                for i in combos[0][1:]:
                    extra = Parsed(p.source, p.kind, part=i.data["invoice_id"], vendor_id=p.vendor_id,
                                   dated=p.dated, text=p.text, method=p.method,
                                   data=dict(p.data, invoice_id=i.data["invoice_id"],
                                             amount=i.data["amount"]),
                                   confidence=p.confidence, warnings=list(p.warnings))
                    parsed.append(extra)
            else:
                p.flag("can't tell which invoice this is about", human=True, conf=0.4)
                p.kind = None
        else:
            p.flag("can't tell which invoice this is about", human=True, conf=0.4)
            p.kind = None

    for p in kinds[SourceKind.manager_reply] + kinds[SourceKind.vendor_notice]:
        if p.kind is not None and not p.data.get("invoice_id"):
            p.kind = None
    return parsed
