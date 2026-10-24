"""Constrained free-text parser. Matches a few known sentence shapes and refuses the rest."""

from __future__ import annotations

import re
from datetime import date

from .console import ConsoleError, JudgeConsole

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE = re.compile(
    r"(?P<iso>\d{4}-\d{2}-\d{2})|(?P<mon>[A-Za-z]{3,9})\.?\s+(?P<d1>\d{1,2})(?:st|nd|rd|th)?\b"
    r"|(?P<m2>\d{1,2})/(?P<d2>\d{1,2})")

EXAMPLES = [
    "The electrician's invoice arrived Apr 15",
    "Keyway says they paid the electrician",
    "Rio Grande Plumbing confirmed they were paid",
    "Summit Heating & Air asks to change bank details",
]


def parse_date(text: str, year: int) -> date | None:
    m = _DATE.search(text)
    if not m:
        return None
    try:
        if m["iso"]:
            return date.fromisoformat(m["iso"])
        if m["mon"] and m["mon"][:3].lower() in _MONTHS:
            return date(year, _MONTHS[m["mon"][:3].lower()], int(m["d1"]))
        if m["m2"]:
            return date(year, int(m["m2"]), int(m["d2"]))
    except ValueError:
        pass
    return None


def _resolve_invoice(text: str, c: JudgeConsole, manager_id: str | None,
                     prefer_open: bool = False) -> str:
    low = text.lower()
    invs = c.invoices()
    for d in invs:                                        # explicit invoice id wins
        if d.data["invoice_id"].lower() in low:
            return d.data["invoice_id"]
    mgrs = c.managers()
    named = [m for m, v in mgrs.items() if m in low or v["name"].split()[0].lower() in low]
    scope = manager_id or (named[0] if len(named) == 1 else None)
    vendors = c.vendors()
    hit = {v for v, info in vendors.items()
           if info["name"].lower() in low or info["trade"] in low}
    cands = [d for d in invs if d.vendor_id in hit and (scope is None or d.manager_id == scope)]
    if len(cands) > 1 and prefer_open:      # a "paid" claim or a receipt-date fix is about an open item
        still_open = {l.invoice_id for m in {d.manager_id for d in cands}
                      for l in c.rails.ledger(m) if l.paid_amount < l.amount}
        cands = [d for d in cands if d.data["invoice_id"] in still_open] or cands
    if len(cands) == 1:
        return cands[0].data["invoice_id"]
    if not cands:
        raise ConsoleError("couldn't tell which invoice you mean", EXAMPLES)
    raise ConsoleError(
        "that matches several invoices; name the manager or the invoice id",
        sorted(d.data["invoice_id"] for d in cands)[:8])


def parse(text: str, c: JudgeConsole, manager_id: str | None = None) -> tuple[str, dict]:
    low = text.lower().strip()
    if not low:
        raise ConsoleError("type what changed", EXAMPLES)

    if re.search(r"\b(change|update|new)\b.*\bbank\b", low):
        vendors = c.vendors()
        v = [i for i, info in vendors.items() if info["name"].lower() in low]
        if len(v) != 1:
            raise ConsoleError("name the vendor that is changing bank details", EXAMPLES)
        return "vendor_requests_bank_change", {"vendor_id": v[0]}

    if re.search(r"\b(arrived|was received|came in|received on)\b", low):
        when = parse_date(text, c.rails.now().year)
        if not when:
            raise ConsoleError("I need the date it arrived (e.g. Apr 1 or 2026-04-01)", EXAMPLES)
        return "invoice_received_correction", {
            "invoice_id": _resolve_invoice(text, c, manager_id, prefer_open=True),
            "date": when.isoformat()}

    if re.search(r"\b(vendor|they|it)\b.*\b(confirm\w*)\b.*\bpaid\b|\bconfirm\w*\b.*\bpaid\b", low):
        when = parse_date(text, c.rails.now().year)
        params = {"invoice_id": _resolve_invoice(text, c, manager_id, prefer_open=True)}
        if when:
            params["paid_on"] = when.isoformat()
        return "vendor_confirms_paid", params

    if re.search(r"\b(we|they|manager|says?|said|replied?)\b.*\bpaid\b", low):
        return "manager_replies_paid", {"invoice_id": _resolve_invoice(text, c, manager_id, True)}

    raise ConsoleError("I only act on facts I can record as an event. Try one of these:", EXAMPLES)
