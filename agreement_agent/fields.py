"""What a document must contain, and rule-based finders for each field (free, no model).

Every finder returns candidates as (value, quote): the quote is a verbatim sentence from the
document. The agent may only record a value whose quote is really in the document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Parsed


@dataclass(frozen=True)
class Spec:
    name: str
    label: str
    hint: str                     # what to look for (also given to the model)
    required: bool = True


AGREEMENT = [
    Spec("owner_name", "Owner company name", "the paying party: 'between X (\"Owner\"/\"Client\") and ...'"),
    Spec("vendor_name", "Vendor company name", "the party doing the work: '... and Y (\"Vendor\"/\"Contractor\")'"),
    Spec("payment_due", "Payment due date or terms", "when payment is due: 'within N days of invoice', 'Net 30', 'due on <date>'"),
    Spec("payment_amount", "Payment amount or rate", "the price, fee or rate: '$4,800', '$95 per hour'"),
    Spec("payment_info", "Payment information", "how to pay: ACH/wire with bank, routing and account, check payable to, remit-to address"),
    Spec("effective_date", "Effective date", "when the agreement starts", required=False),
    Spec("reminder_terms", "Reminder or grace terms",
         "how long to wait before following up on a late payment: 'grace period of N days', "
         "'reminder every N days', 'late fee after N days'", required=False),
    Spec("signatures", "Signatures", "signed by both parties: 'By:', 'Signature', 'Name/Title/Date'", required=False),
]
INVOICE = [
    Spec("vendor_name", "Vendor company name", "who issued the invoice (top of the document, 'From', 'Remit to')"),
    Spec("owner_name", "Billed company name", "who is billed: 'Bill to', 'Customer'"),
    Spec("invoice_number", "Invoice number", "'Invoice #', 'Invoice No.'"),
    Spec("payment_amount", "Amount due", "'Total', 'Amount due', 'Balance due'"),
    Spec("payment_due", "Payment due date or terms", "'Due date', 'Terms: Net 30', 'Due upon receipt'"),
    Spec("payment_info", "Payment information", "how to pay: bank, routing and account, check payable to, remit-to address"),
]
SPECS = {"agreement": AGREEMENT, "invoice": INVOICE, "other": AGREEMENT}

_ABBR = "".join(rf"(?<!\b{a}\.)" for a in ("No", "Inc", "Co", "Corp", "Ltd", "St", "Mr", "Ms", "Dr", "Jr"))
_SENT = re.compile(_ABBR + r"(?<=[.;])\s+(?=[A-Z(])|\n")
_SUFFIX = r"(?:LLC|L\.L\.C\.|Inc\.?|Co\.|Corp\.?|Corporation|Company|Ltd\.?|LLP|LP)"
_TRADE = r"(?:Group|Services|Homes|Plumbing|Electric|HVAC|Roofing|Landscaping|Cleaning|Management|Construction)"
# A company name: capitalised words ending in a trade word and/or a legal suffix ("Rio Grande Plumbing LLC")
_ORG = r"((?:[A-Z][\w&'\-]*\s+){0,5}?(?:" + _TRADE + r"(?:,?\s+" + _SUFFIX + r")?|[A-Z][\w&'\-]*,?\s+" + _SUFFIX + r"))"
_OWNER_ROLE = r"(?:Owner|Client|Company|Customer|Buyer)"
_VENDOR_ROLE = r"(?:Vendor|Contractor|Service Provider|Provider|Supplier|Consultant|Property Manager|Manager)"
_MONEY = r"\$\s?\d[\d,]*(?:\.\d{2})?"
_UNIT = r"(?:\s*(?:per|/)\s*\w+(?:\s+per\s+\w+)?)"      # "$350 per unit per visit"
_DATE = (r"(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
         r"|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text) if s and s.strip()]


def _units(text: str) -> list[str]:
    """Sentences, then each short label line joined with the line after it: many PDFs put a label
    and its value on separate lines ("TOTAL DUE" / "$1,240.00"). A joined pair still passes the
    quote guard, which compares with whitespace collapsed."""
    out = sentences(text)
    lines = [ln.strip() for ln in text.splitlines()]
    for a, b in zip(lines, lines[1:]):
        if a and b and len(a) <= 40:
            out.append(f"{a} {b}")
    return out


def _first(pattern: str, text: str, flags=re.IGNORECASE) -> list[tuple[str, str]]:
    out, seen = [], set()
    for s in _units(text):
        if m := re.search(pattern, s, flags):
            hit = ((m.group(1) if m.groups() else m.group(0)).strip(" .,;:"), s)
            if hit[0] not in seen:
                seen.add(hit[0])
                out.append(hit)
    return out


def _parties(p: Parsed, role: str) -> list[tuple[str, str]]:
    out = []
    head = " ".join(p.header) + "\n" + p.text[:2500]
    # a short gap, or a longer description set off by a comma (", a Colorado limited liability
    # company with offices at ..."); never across another party's (...) label
    pat = _ORG + r"(?:,\s*[^()\n]{0,180}|\s*,?\s*[^()\n]{0,60})\(\s*(?:the\s+)?[\"“]?" + role + r"[\"”]?\s*\)"
    for s in sentences(head.replace("\n", " ")):
        for m in re.finditer(pat, s):
            out.append((m.group(1).strip(" ,"), s))
    return out


def find(name: str, p: Parsed) -> list[tuple[str, str]]:
    """Rule-based candidates for one field: [(value, verbatim quote)], best first."""
    t = p.text
    if name == "owner_name":
        c = _parties(p, _OWNER_ROLE)
        if not c:
            c = _first(r"(?:bill(?:ed)? to|customer|sold to)\s*:?\s*" + _ORG, t)
        return c
    if name == "vendor_name":
        c = _parties(p, _VENDOR_ROLE)
        if not c and p.kind == "invoice":
            c = _first(r"(?:remit to|from|payable to)\s*:?\s*" + _ORG, t) or \
                [(m.group(1), line) for line in p.header[:3] if (m := re.search(_ORG, line, re.IGNORECASE))]   # "SUMMIT ROOFING CO."
        return c
    if name == "payment_due":
        pats = [r"(within\s+(?:\w+\s+)?\(?\d+\)?\s+(?:business\s+|calendar\s+)?days?\b[^.;]{0,60})",
                r"\b(net\s*\d{1,3})\b", r"(due\s+(?:on|by)\s+" + _DATE + ")",
                r"(due\s+date\s*:?\s*" + _DATE + ")", r"(payable\s+(?:on|by)\s+" + _DATE + ")",
                r"(due\s+(?:up)?on\s+receipt)", r"(payment\s+(?:is\s+)?due\s+(?:on\s+)?(?:the\s+)?(?:\d{1,2}(?:st|nd|rd|th)?|first|last|fifteenth)\s+(?:day\s+)?of\s+each\s+month)"]
        out = []
        for pat in pats:
            out += [(v, s) for v, s in _first(pat, t) if re.search(r"pay|due|invoice|terms", s, re.IGNORECASE)]
        return out
    if name == "payment_amount":
        pats = [r"(?:total|amount due|balance due|contract price|fee|price|compensation|sum of|rate of|amount of)[^$\n]{0,40}(" + _MONEY + _UNIT + r"?)",
                r"\bpay\b[^$\n]{0,40}(" + _MONEY + _UNIT + r"?)",
                r"(" + _MONEY + r"\s*(?:per|/)\s*(?:hour|hr|month|visit|job|day|unit|property))"]
        out = []
        for pat in pats:
            out += _first(pat, t)
        return out
    if name == "payment_info":
        pats = [r"((?:ach|wire|bank transfer|direct deposit|electronic funds transfer|eft)\b[^.]{0,120}(?:routing|account|aba|bank)[^.]{0,80})",
                r"((?:routing|aba)\s*(?:number|no\.?|#)?\s*:?\s*\d{9}[^.\n]{0,80})",
                r"((?:checks?|cheques?)\s+(?:shall be\s+|should be\s+)?(?:made\s+)?payable\s+to\s+[^.\n]{3,80})",
                r"(remit(?:tance)?\s+(?:to|address)\s*:?\s*[^.\n]{5,120})",
                r"((?:zelle|venmo|paypal)\s+[^.\n]{3,80})"]
        out = []
        for pat in pats:
            out += _first(pat, t)
        return out
    if name == "effective_date":
        return _first(r"(?:effective|dated|made (?:and entered into )?(?:as of|on))\s*(?:date\s*)?(?:as of\s*)?:?\s*(" + _DATE + ")", t)
    if name == "reminder_terms":
        return _first(r"((?:grace period|reminders?|follow[- ]?ups?|late (?:fee|charge))[^.]{0,80}?"
                      r"\b(?:\w+ \()?\d+\)? (?:business |calendar )?days?\b[^.]{0,40})", t)
    if name == "signatures":
        # "By: Dana Ortiz, Controller ..." counts; "By: ________" is a blank signature line
        hits = [(v, q) for v, q in _first(r"\b(?i:by|signature|signed)\s*:\s*([A-Z][A-Za-z.'\-]+(?: [A-Z][A-Za-z.'\-]+)+)", t, 0)]
        return hits[:2] if len(hits) >= 2 else []
    if name == "invoice_number":
        return _first(r"invoice\s*(?:#|no\.?|number)\s*:?\s*([A-Z0-9][\w-]{2,20})", t)
    return []


def search(p: Parsed, query: str, limit: int = 8) -> list[dict]:
    """Keyword search over the document: sentences containing every word of the query."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
    out = []
    for s in sentences(p.text):
        low = s.lower()
        if words and all(w in low for w in words):
            out.append({"text": s[:300], "where": p.where(s) or ""})
        if len(out) >= limit:
            break
    return out
