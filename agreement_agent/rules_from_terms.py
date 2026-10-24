"""Turn a reviewed agreement into the rules every later payment to that vendor is checked against.

Code, not the model, writes the rules: each one is parsed from a verbatim quote the review already
verified, keeps that quote and its location, and says in plain words what will be checked. A
required term that is missing becomes a blocking rule: no payment until the owner gets it.

    pay_within_days   payment date <= invoice received + N days (calendar or business)
    amount            invoice / payment amount matches the agreed fee or rate
    payee             the bank payment names this vendor
    pay_to_account    the bank payment goes to this bank, routing and account
    hold_payment      a required term is missing or unclear: do not pay yet
"""
from __future__ import annotations

import re

from .models import FieldResult, Review

_WORDS = {"seven": 7, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty-five": 45,
          "forty five": 45, "sixty": 60, "ninety": 90}
_PER = {"hour": "hour", "hourly": "hour", "month": "month", "monthly": "month", "week": "week",
        "weekly": "week", "day": "day", "daily": "day", "visit": "visit", "year": "year",
        "annual": "year", "annually": "year"}
REQUIRED = ("payment_due", "payment_amount", "payment_info")


def _days(text: str) -> int | None:
    t = text.lower()
    for pat in (r"\((\d+)\)\s*(?:business\s+|calendar\s+)?days", r"(\d+)\s*(?:business\s+|calendar\s+)?days",
                r"net\s*(\d+)"):
        if m := re.search(pat, t):
            return int(m.group(1))
    for word, n in _WORDS.items():
        if re.search(rf"\b{word}\b[^.]{{0,12}}days", t):
            return n
    return None


def _amount(text: str) -> tuple[float, str] | None:
    m = re.search(r"\$\s?([\d,]+(?:\.\d{1,2})?)", text)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    t = text.lower()
    per = re.search(r"\bper\s+(hour|month|week|day|visit|year)\b", t)
    if per:
        return value, per.group(1)
    for word, unit in _PER.items():
        if re.search(rf"\b{word}\b", t):
            return value, unit
    return value, "fixed"


def _account(text: str) -> dict:
    bank = re.search(r"(?:to|at|with)\s+([A-Z][\w&.' ]*?Bank)\b", text)
    routing = re.search(r"routing(?:\s+(?:number|no\.?|#))?\s*:?\s*(\d{9})", text, re.IGNORECASE)
    account = re.search(r"account(?:\s+(?:number|no\.?|#))?\s*:?\s*(\d{4,17})", text, re.IGNORECASE)
    return {k: v for k, v in {"bank": bank and bank.group(1).strip(),
                              "routing": routing and routing.group(1),
                              "account": account and account.group(1)}.items() if v}


def _rule(review: Review, f: FieldResult | None, kind: str, params: dict, check: str,
          status: str = "active") -> dict:
    suffix = f":{params['term']}" if kind == "hold_payment" else ""
    return {"rule_id": f"{review.doc_id}:{kind}{suffix}", "rule_type": kind, "params": params,
            "check": check, "status": status,
            "source_quote": f.quote if f else None, "location": f.location if f else None}


def rules_for(review: Review) -> list[dict]:
    if review.kind != "agreement":
        return []
    by = {f.name: f for f in review.fields}
    found = {n: f for n, f in by.items() if f.status == "found" and f.value}
    vendor = found["vendor_name"].value if "vendor_name" in found else None
    rules: list[dict] = []

    if vendor:
        rules.append(_rule(review, found["vendor_name"], "payee", {"vendor": vendor},
                           f"Every bank payment for this agreement must name {vendor}."))
    if f := found.get("payment_due"):
        text = f"{f.value} {f.quote or ''}"
        if (days := _days(text)) is not None:
            basis = "business" if "business" in text.lower() else "calendar"
            start = "invoice receipt" if "receipt" in text.lower() else "invoice date"
            rules.append(_rule(review, f, "pay_within_days",
                               {"days": days, "basis": basis, "from": start,
                                "undisputed_only": "undisputed" in text.lower()},
                               f"Pay within {days} {basis} days of {start}; chase after that."))
        else:
            rules.append(_rule(review, f, "hold_payment", {"term": "payment_due"},
                               f"Due terms '{f.value}' could not be read as a day count; "
                               "a controller must set the deadline.", "needs_review"))
    if f := found.get("payment_amount"):
        parsed = _amount(f"{f.value} {f.quote or ''}")
        if parsed:
            value, per = parsed
            check = (f"Each payment must equal ${value:,.2f}." if per == "fixed" else
                     f"Invoices are billed at ${value:,.2f} per {per}; flag any other rate.")
            rules.append(_rule(review, f, "amount", {"amount": value, "per": per,
                                                     "extras_allowed": "approved materials"
                                                     in ((f.value or "") + (f.quote or "")).lower()},
                               check))
    if f := found.get("payment_info"):
        acct = _account(f"{f.value} {f.quote or ''}")
        if acct:
            where = ", ".join(f"{k} {v}" for k, v in acct.items())
            rules.append(_rule(review, f, "pay_to_account", acct,
                               f"Payments must go to {where}; any change needs controller "
                               "approval and identity checks."))
    for name in REQUIRED:
        f = by.get(name)
        if not f or f.status != "found":
            label = f.label if f else name
            rules.append(_rule(review, f, "hold_payment", {"term": name},
                               f"{label} is {f.status if f else 'missing'}: hold payment until "
                               "the owner receives it.", "blocking"))
    return rules
