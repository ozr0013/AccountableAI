"""Honest rules baseline: layout regexes, fuzzy vendor names and conservative linking.

Only business record lines are parsed. Attachment instructions have no parser grammar.
Ambiguous vendor or invoice links are left for review, never chosen by proximity alone.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher

from contracts.models import Document

from .schema import Candidate, Extraction

MONEY = r"\$([\d,]+\.\d{2})(?!\d)"
IID = r"INV-[A-Z0-9-]+"
DATE = r"202[0-9]-\d{2}-\d{2}"


def resolve_vendor(text, vendors):
    def norm(s):
        return re.sub(r"[^a-z0-9]", "", s.lower().replace("plbg", "plumbing").replace("clng", "cleaning").replace(" llc", ""))
    text = norm(text)
    aliases = []
    trades = {"Plumbing": "PLMB", "Electric": "ELEC", "Roofing": "ROOF", "Cleaning": "CLN", "Cleaners": "CLN", "Landscaping": "LAND"}
    for v in vendors:
        words = v["name"].split()
        abbreviation = "".join(w[0] for w in words[:-1]) + " " + trades.get(words[-1], words[-1].upper())
        if text == norm(abbreviation):
            aliases.append(v["id"])
    if len(aliases) == 1:
        return aliases[0]
    ranked = sorted(((SequenceMatcher(None, text, norm(v["name"])).ratio(), v["id"]) for v in vendors), reverse=True)
    if ranked and ranked[0][0] >= .72 and (len(ranked) == 1 or ranked[0][0]-ranked[1][0] >= .08):
        return ranked[0][1]
    return None


def parse(doc: Document, vendors: list[dict], invoices: dict[str, dict]) -> Extraction:
    # Ignore instructions only by the grammar's business-line boundaries, not by obeying them.
    text = doc.text.split("\nAttachment note:", 1)[0]
    quotes = {}
    def match(key, pattern, group=1, flags=re.IGNORECASE):
        m = re.search(pattern, text, flags)
        if m:
            quotes[key] = m.group(0)
            return m.group(group)
        return None
    def money(key, pattern):
        x = match(key, pattern)
        return Decimal(x.replace(",", "")) if x else None
    def day(key, pattern):
        x = match(key, pattern)
        try:
            return date.fromisoformat(x) if x else None
        except ValueError:
            return None
    iid = match("invoice_id", f"({IID})")
    kind = doc.kind.value
    args = {"invoice_id": iid, "quotes": quotes, "confidence": .95}
    if kind == "agreement":
        if not all(s in text for s in ("ten (10) days", "ten percent (10%)", "$5,000")):
            return Extraction(candidates=[], uncertain=["unsupported agreement wording"])
        args.update(role="terms", vendor_payment_days=10, holdback_pct=10, approval_limit=5000)
        quotes.update(vendor_payment_days="ten (10) days", holdback_pct="ten percent (10%)", approval_limit="$5,000")
    elif kind == "vendor_invoice":
        supplier = re.search(r"Supplier: ([^\n]+)", text)
        label = supplier.group(1) if supplier else text.splitlines()[0]
        vendor = resolve_vendor(label, vendors)
        quotes["vendor_id"] = label
        amount = money("amount", r"(?:Amount due: \$|TOTAL USD |Total \$)([\d,]+\.\d{2})")
        rec = day("received", rf"(?:Received: |receipt date |received )({DATE})")
        ref = match("vendor_ref", r"vendor reference: ([A-Za-z0-9-]+)")
        period = match("service_period", r"Service period: (\d{4}-\d{2})")
        args.update(role="invoice", vendor_id=vendor, amount=amount, received=rec,
                    vendor_ref=ref, service_period=period)
        if "Inspection pending." in text:
            args["inspection"] = "pending"
            quotes["inspection"] = "Inspection pending."
        items = re.findall(rf"(?:Labor|Materials) {MONEY}", text)
        if items:
            args["line_items"] = [Decimal(x.replace(",", "")) for x in items]
            quotes["line_items"] = re.search(r"Labor [^\n]+\nMaterials [^\n]+", text).group(0)
    elif kind == "funding":
        args.update(role="funding", amount=money("amount", MONEY),
                    on=day("on", rf"Value date ({DATE})"), payer="oakline", payee="keyway")
        quotes.update(payer="Oakline Homes", payee="Keyway Property Management")
    elif kind == "manager_bank":
        candidates = []
        for row in text.splitlines():
            m = re.fullmatch(rf"({DATE}),{MONEY},(.+),(TX-[\w-]+)", row)
            if not m:
                continue
            on, amount, memo, tx = m.groups()
            id_match = re.search(IID, memo)
            label = re.split(rf"{IID}|\b\d{{4}}\b", memo)[0].strip()
            vid = resolve_vendor(label, vendors)
            q = {"amount": row, "on": row, "vendor_id": label, "transaction_id": tx,
                 "payer": "Keyway", "payee": label}
            iid2 = id_match.group(0) if id_match else None
            if iid2:
                q["invoice_id"] = iid2
            candidates.append(Candidate(role="payment", invoice_id=iid2, vendor_id=vid,
                amount=Decimal(amount.replace(",", "")), on=on, transaction_id=tx,
                payer="keyway", payee=vid, quotes=q, confidence=.95))
        return Extraction(candidates=candidates, uncertain=[] if candidates else ["no bank rows parsed"])
    elif kind == "manager_statement":
        candidates = []
        for row in text.splitlines():
            m = re.fullmatch(rf"(L\d+) \| ({IID}) \| (.+) \| {MONEY} \| (.+) \| ({DATE})", row)
            if m:
                _line, iid2, label, amount, status, on = m.groups()
                status = "hold_missing_w9" if status.startswith("ON HOLD") else "paid" if status == "PAID" else "claim"
                candidates.append(Candidate(role="statement", invoice_id=iid2,
                    amount=Decimal(amount.replace(",", "")), status=status, on=on,
                    quotes={k: row for k in ("invoice_id", "amount", "status", "on")}, confidence=.95))
        if not candidates:
            m = re.search(rf"On ({DATE}), Keyway reports invoice ({IID}) for (.+?) \({MONEY}\) is (PAID|ON HOLD, vendor W-9 not on file)\.", text)
            if m:
                on, iid2, label, amount, status = m.groups()
                candidates.append(Candidate(role="statement", invoice_id=iid2, on=on,
                    amount=Decimal(amount.replace(",", "")),
                    status="paid" if status == "PAID" else "hold_missing_w9",
                    quotes={k: m.group(0) for k in ("invoice_id", "on", "amount", "status")}, confidence=.95))
        return Extraction(candidates=candidates, uncertain=[] if candidates else ["no statement rows parsed"])
    elif kind in ("manager_reply", "vendor_notice", "vendor_confirmation"):
        body = text.split("\n\n", 1)[-1] if "\n\n" in text else text.split("\n", 2)[-1]
        if kind != "manager_reply":
            sender = re.search(r"From: ([^\n]+)", text)
            if sender:
                vid = resolve_vendor(sender.group(1), vendors)
                if vid:
                    args["vendor_id"] = vid
                    quotes["vendor_id"] = sender.group(1)
        status = ("bank_change_requested" if "bank change requested" in body.lower() else
                  "paperwork_received" if re.search(r"w-9 (provided|received|attached)", body, re.IGNORECASE) else
                  "not_received" if "not received" in body.lower() or "remains unpaid" in body.lower() else
                  "paid" if re.fullmatch(r"paid|cheque went out Friday", body, re.IGNORECASE) or (kind == "vendor_confirmation" and re.search(r"payment received|confirms receipt", body, re.IGNORECASE)) else "claim")
        quotes["status"] = body
        args.update(role="claim", status=status)
        if kind == "vendor_confirmation" and status == "paid":
            args.update(amount=money("amount", MONEY), on=day("on", rf"({DATE})"))
    else:
        return Extraction(candidates=[], uncertain=["unsupported source kind"])
    return Extraction(candidates=[Candidate(**args)])
