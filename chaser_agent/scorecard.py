"""Planted errors against what the system caught: the demo's proof that it works.

The answer key comes from the data generator (chaser_agent/demo_feed.py) plus the world's own
planted cases; the agents never see it. For every planted invoice whose trouble has become
visible in the books by today, this checks whether the system caught it the way a finance team
would expect, and for every clean invoice whether it stayed quiet (no false alarm).

    caught       the expected finding or action is on record
    missed       the trouble is visible in the books but nothing caught it
    not yet      the trouble has not happened yet on the demo calendar (no payment, not due)
    clear        a clean invoice with no alarm raised (correct)
    false alarm  a clean invoice that was flagged, chased or reported
"""
from __future__ import annotations

from datetime import date

from . import daily

# The demo world's own planted cases (world/, seed 13), on top of the generated month.
WORLD_KEY = {
    "INV-KW-0301": "late",          # unpaid; the storyboard pays it late on Apr 29
    "INV-KW-0101": "partial",
    "INV-KW-0601": "unfunded",
    "INV-KW-0201": "unpaid",
    "INV-KW-0402": "unpaid",
    "INV-KW-0901": "unpaid",
    "INV-KW-0701": "unpaid",
}

EXPECT = {
    "late": "late_payment finding on the case",
    "unpaid": "chased: at least one reminder once overdue",
    "partial": "partial_payment finding, and the balance chased",
    "wrong_vendor": "wrong_vendor finding (payment to another vendor not counted)",
    "duplicate": "overpayment finding",
    "statement_amount": "statement_mismatch finding and the note to Keyway queued",
    "unfunded": "not chased (Oakline never funded it)",
    "on_time": "no alarm: verified paid, no reminder, no flag",
}
CATEGORY = {"late": "Paid late", "unpaid": "Never paid", "partial": "Short payment",
            "wrong_vendor": "Paid to wrong vendor", "duplicate": "Paid twice",
            "statement_amount": "Statement amount wrong", "unfunded": "Not funded",
            "on_time": "Clean (should stay quiet)"}
ALARMS = {"late_payment", "partial_payment", "overpayment", "wrong_vendor", "statement_mismatch",
          "statement_wrong_vendor", "wrong_payee", "wrong_payment_account"}


def _judge(fate, line, rows, case, actions, statement_in) -> tuple[str, str]:
    codes = {f["code"] for f in (case or {}).get("findings", [])}
    reminders = [a for a in actions if a["action"] == "reminder"]
    flags = [a for a in actions if a["action"] == "flag"]
    outbox = {o["kind"] for o in (case or {}).get("outbox", [])}
    paid_rows = bool(rows)
    if fate == "on_time":
        if not paid_rows:
            return "not yet", "no payment yet"
        alarms = sorted(codes & ALARMS) + [f"reminder {a['level']}" for a in reminders] + \
            [f"flag {a['level']}" for a in flags]
        return ("false alarm", ", ".join(alarms)) if alarms else ("clear", "verified paid, nothing raised")
    if fate == "unfunded":
        return ("false alarm", "chased although unfunded") if reminders else ("clear", "not chased")
    if fate == "late" and not paid_rows:
        fate = "unpaid"             # not paid yet: until it is, the test is whether it gets chased
    if fate == "unpaid":
        if line is None or line.paid_amount > 0:
            return "not yet", "paid since"
        if not reminders:
            return ("missed", "overdue, no reminder") if _overdue(line, case) else ("not yet", "not due yet")
        levels = ", ".join(a["level"] for a in reminders)
        raised = " and raised to Oakline" if any(a["action"] == "escalate" for a in actions) else ""
        return "caught", f"chased: {levels}{raised}"
    if not paid_rows:
        return "not yet", "no payment yet"
    want = {"late": "late_payment", "partial": "partial_payment", "wrong_vendor": "wrong_vendor",
            "duplicate": "overpayment", "statement_amount": "statement_mismatch"}[fate]
    if fate == "statement_amount" and not statement_in:
        return "not yet", "statement not received yet"
    if want in codes or (fate == "wrong_vendor" and any(a["level"] == "payment_to_other_vendor" for a in flags)):
        extra = ""
        if fate == "statement_amount" and "statement_notice" in outbox:
            extra = "; note to Keyway queued"
        if fate in ("partial", "wrong_vendor") and reminders:
            extra = f"; balance chased ({', '.join(a['level'] for a in reminders)})"
        return "caught", f"{want}{extra}"
    return "missed", f"expected {want}; found {sorted(codes) or 'nothing'}"


def _overdue(line, case) -> bool:
    return (case or {}).get("state") == "overdue"


def score(rails, mirror, today: date, fates: dict[str, str], manager_id: str = "keyway") -> dict:
    from . import api, connected
    key = {**WORLD_KEY, **fates}
    ledger = {l.invoice_id: l for l in rails.ledger(manager_id)}
    bank = daily.bank_rows_by_invoice(rails, manager_id, today)
    cases = {c["invoice_id"]: c for c in connected.cases(rails, manager_id)}
    actions: dict[str, list[dict]] = {}
    for h in api.history(mirror):
        actions.setdefault(h["invoice_id"], []).append(h)
    statements = {r.get("invoice_id") for d in rails.documents(manager_id)
                  if d.kind.value == "manager_statement" for r in d.data.get("lines", [])}
    rows = []
    for inv, fate in sorted(key.items()):
        if inv not in ledger:
            continue                    # its invoice has not arrived yet on the demo calendar
        line, _ = daily.verified_line(ledger[inv], bank.get(inv, []))
        result, detail = _judge(fate, line, bank.get(inv, []), cases.get(inv), actions.get(inv, []),
                                inv in statements)
        rows.append({"invoice_id": inv, "planted": fate, "category": CATEGORY[fate],
                     "expected": EXPECT[fate], "result": result, "detail": detail})
    by: dict[str, dict] = {}
    for r in rows:
        b = by.setdefault(r["planted"], {"category": r["category"], "planted": 0, "caught": 0,
                                         "missed": 0, "not_yet": 0, "clear": 0, "false_alarm": 0})
        b["planted"] += 1
        b[r["result"].replace(" ", "_")] += 1
    caught = sum(b["caught"] for b in by.values())
    missed = sum(b["missed"] for b in by.values())
    false_alarms = sum(b["false_alarm"] for b in by.values())
    clear = sum(b["clear"] for b in by.values())
    return {"as_of": today.isoformat(), "caught": caught, "missed": missed, "false_alarms": false_alarms,
            "clear": clear, "not_yet": sum(b["not_yet"] for b in by.values()),
            "recall": round(caught / (caught + missed), 3) if caught + missed else None,
            "precision": round(caught / (caught + false_alarms), 3) if caught + false_alarms else None,
            "by_category": [{"planted_as": k, **v} for k, v in sorted(by.items(), key=lambda x: x[0] == "on_time")],
            "rows": rows}
