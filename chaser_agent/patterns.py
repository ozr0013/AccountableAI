"""How regularly the management company pays each vendor late.

For every paid invoice: deadline = the vendor's agreement rule (`pay_within_days`) when one is
saved, else the ledger's due date from confirmed terms. Late = paid after the deadline.
A vendor is flagged `chronic` when at least half of three or more paid invoices were late.
"""
from __future__ import annotations

from datetime import date

from .daily import _rules_by_vendor, add_days, rules_for

CHRONIC_MIN_PAID = 3
CHRONIC_RATE = 0.5


def _deadline(line, rules: list[dict]) -> tuple[date | None, str]:
    term = next((r for r in rules if r["rule_type"] == "pay_within_days"
                 and r["status"] == "active"), None)
    if term and line.invoice_received:
        p = term["params"]
        return add_days(line.invoice_received, int(p["days"]), p.get("basis", "calendar")), "agreement"
    return line.due_date, "ledger terms"


def vendor_patterns(rails, rule_rows: list[dict], today: date, manager_id: str = "keyway"
                    ) -> list[dict]:
    from workflow.names import names_from
    name = names_from(rails)
    rules = _rules_by_vendor(rule_rows)
    stats: dict[str, dict] = {}
    for line in rails.ledger(manager_id):
        if line.duplicate_of:
            continue
        vendor = name(line.vendor_id)
        due, source = _deadline(line, rules_for(rules, vendor, name(manager_id)))
        s = stats.setdefault(vendor, {"vendor": vendor, "vendor_id": line.vendor_id, "paid": 0,
                                      "late": 0, "days_late": [], "overdue_unpaid": [],
                                      "deadline_source": source, "late_invoices": []})
        if due is None:
            continue
        if line.paid_on and line.paid_amount > 0:
            s["paid"] += 1
            days = (line.paid_on - due).days
            if days > 0:
                s["late"] += 1
                s["days_late"].append(days)
                s["late_invoices"].append({"invoice_id": line.invoice_id, "due": due.isoformat(),
                                           "paid_on": line.paid_on.isoformat(), "days_late": days})
        elif today > due:
            s["overdue_unpaid"].append({"invoice_id": line.invoice_id, "due": due.isoformat(),
                                        "days_overdue": (today - due).days})
    out = []
    for s in stats.values():
        late_days = s.pop("days_late")
        rate = s["late"] / s["paid"] if s["paid"] else 0.0
        out.append({**s, "late_rate": round(rate, 2),
                    "avg_days_late": round(sum(late_days) / len(late_days), 1) if late_days else 0.0,
                    "max_days_late": max(late_days, default=0),
                    "chronic": s["paid"] >= CHRONIC_MIN_PAID and rate >= CHRONIC_RATE})
    return sorted(out, key=lambda v: (-v["late_rate"], -v["avg_days_late"], v["vendor"]))


def describe(pattern: dict | None) -> str:
    if not pattern or not pattern["paid"]:
        return "no payment history"
    text = (f"{pattern['late']} of {pattern['paid']} past payments late"
            f" (avg {pattern['avg_days_late']} days)")
    return ("chronically late: " + text) if pattern["chronic"] else text
