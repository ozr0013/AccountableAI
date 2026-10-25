"""Seeded daily paperwork for the time-lapse demo: what lands in Oakline's and Keyway's inboxes.

Each simulated day brings new vendor invoices; Oakline funds them a day or two later; Keyway pays
the vendors (mostly on time, some late, some short, some to the wrong vendor, some twice, a few
never); vendors confirm receipt; at month end Keyway sends a statement, a few of whose lines
disagree with what Oakline approved or claim payments the bank does not show.

The agents are never told any of this: they only see the documents as they arrive, exactly like
the rest of the books. Fates are drawn once from a fixed seed, so every run sees the same month.
All companies and amounts are fictional.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from contracts.models import Document, SourceKind

SEED = 2026
FIRST_NUMBER = 2001
NEW_PER_WEEKDAY = (3, 5)
NEW_PER_WEEKEND_DAY = (0, 1)
# How Keyway handles each funded invoice (weights add to 100).
FATES = (("on_time", 75), ("late", 12), ("unpaid", 5), ("partial", 4), ("wrong_vendor", 2),
         ("duplicate", 2))
STATEMENT_MISMATCHES = (0.5, 1.1)   # stated / approved, on two lines (like $620 for $1,240)
STATEMENT_LAG_DAYS = 4              # the statement covers payments up to `end` minus this
UNFUNDED_PERCENT = 3
TERMS_DAYS = 10            # the management agreement: Keyway pays within 10 days of receipt


@dataclass(frozen=True)
class Vendor:
    vendor_id: str
    name: str
    service: str
    typical: float
    identity: dict          # payee/bank details the vendor's own agreement requires, if any


def _money(x: float) -> float:
    return round(x * 20) / 20       # to the nickel, like the rest of the books


def _usd(x: float) -> str:
    return f"${x:,.2f}"


def vendors(rails, mirror, manager_id: str = "keyway") -> list[Vendor]:
    """Keyway's existing vendors with their usual invoice size and service, from the books."""
    import json

    from workflow.names import names_from

    from .daily import norm_name
    name = names_from(rails)
    amounts: dict[str, list[float]] = {}
    service: dict[str, str] = {}
    for d in rails.documents(manager_id):
        if d.kind.value == "vendor_invoice" and d.vendor_id:
            amounts.setdefault(d.vendor_id, []).append(float(d.data.get("amount", 0)))
            words = (d.text or "").split(". ")
            service.setdefault(d.vendor_id, next((w for w in words if w.endswith("service")), "Service"))
    identity: dict[str, dict] = {}
    for r in mirror.query("SELECT * FROM agreement_rule"):
        if r["status"] != "active" or r["rule_type"] not in ("payee", "pay_to_account"):
            continue
        params = json.loads(r["params"]) if isinstance(r["params"], str) else r["params"]
        key = norm_name(r["vendor_name"])
        bag = identity.setdefault(key, {})
        if r["rule_type"] == "payee" and params.get("vendor"):
            bag["payee"] = params["vendor"]
        for f in ("bank", "routing", "account"):
            if params.get(f):
                bag[f] = params[f]
    return [Vendor(v, name(v), service.get(v, "Service"), median(a), identity.get(norm_name(name(v)), {}))
            for v, a in sorted(amounts.items())]


def build(vendor_list: list[Vendor], start: date, end: date, manager_id: str = "keyway",
          seed: int = SEED) -> dict[date, list[Document]]:
    """Documents by the day they arrive, from `start` through `end`."""
    rng = random.Random(seed)
    feed: dict[date, list[Document]] = {}
    bank: dict[date, list[dict]] = {}
    statement: list[dict] = []
    fates: dict[str, str] = {}
    number = FIRST_NUMBER

    def add(day: date, doc: Document) -> None:
        if start <= day <= end:
            feed.setdefault(day, []).append(doc)

    def pay(day: date, row: dict) -> date:
        while day.weekday() >= 5:            # banks post on weekdays
            day += timedelta(days=1)
        bank.setdefault(day, []).append({**row, "on": day.isoformat()})
        return day

    day = start
    while day <= end:
        lo, hi = NEW_PER_WEEKEND_DAY if day.weekday() >= 5 else NEW_PER_WEEKDAY
        for _ in range(rng.randint(lo, hi)):
            v = rng.choice(vendor_list)
            iid = f"INV-KW-{number}"
            number += 1
            amount = _money(v.typical * rng.uniform(0.5, 1.6))
            add(day, Document(
                id=f"doc-inv-{iid.lower()}", kind=SourceKind.vendor_invoice, manager_id=manager_id,
                vendor_id=v.vendor_id, dated=day,
                text=f"{v.name}. Invoice {iid[4:]}. {v.service}. Amount due: {_usd(amount)}. Terms: Net {TERMS_DAYS}.",
                data={"amount": amount, "received": day.isoformat(), "invoice_id": iid}))
            if rng.randrange(100) < UNFUNDED_PERCENT:
                continue                     # Oakline has not funded it; nothing for Keyway to pay
            funded_on = day + timedelta(days=rng.randint(1, 2))
            add(funded_on, Document(
                id=f"doc-funding-{iid.lower()}", kind=SourceKind.funding, manager_id=manager_id, dated=funded_on,
                text=f"Oakline Homes wire to Keyway Property Management, includes {iid} {_usd(amount)}.",
                data={"amount": amount, "funded_on": funded_on.isoformat(), "invoice_id": iid}))
            fate = rng.choices([f for f, _ in FATES], [w for _, w in FATES])[0]
            fates[iid] = fate
            row = {"amount": amount, "vendor_id": v.vendor_id, "invoice_id": iid, **v.identity}
            if fate == "unpaid":
                # Keyway's statement still claims it was paid; the bank never shows it.
                statement.append({"on": (funded_on + timedelta(days=3)).isoformat(), "amount": amount,
                                  "status": "paid", "invoice_id": iid})
                continue
            if fate == "late":
                paid = pay(day + timedelta(days=TERMS_DAYS + rng.randint(2, 10)), row)
            else:
                paid = pay(funded_on + timedelta(days=rng.randint(1, 6)), row)
            if fate == "partial":
                bank[paid][-1]["amount"] = _money(amount * rng.uniform(0.4, 0.8))
            elif fate == "wrong_vendor":
                other = rng.choice([x for x in vendor_list if x.vendor_id != v.vendor_id])
                bank[paid][-1].update({"vendor_id": other.vendor_id, **other.identity})
            elif fate == "duplicate":
                pay(paid + timedelta(days=rng.randint(1, 3)), dict(bank[paid][-1]))
            if fate != "wrong_vendor":
                confirmed = paid + timedelta(days=rng.randint(1, 3))
                got = bank[paid][-1]["amount"]
                add(confirmed, Document(
                    id=f"doc-conf-{iid.lower()}", kind=SourceKind.vendor_confirmation, manager_id=manager_id,
                    vendor_id=v.vendor_id, dated=confirmed,
                    text=f"{v.name} confirms receipt of {_usd(got)} for {iid}.",
                    data={"amount": got, "status": "paid", "paid_on": paid.isoformat(), "invoice_id": iid}))
            statement.append({"on": paid.isoformat(), "amount": amount, "status": "paid", "invoice_id": iid})
        day += timedelta(days=1)

    for posted, payments in sorted(bank.items()):
        add(posted, Document(
            id=f"doc-bank-keyway-live-{posted}", kind=SourceKind.manager_bank, manager_id=manager_id, dated=posted,
            text=f"Keyway Property Management operating account {posted}: {len(payments)} outgoing vendor payments.",
            data={"payments": payments}))
    _statement(add, statement, fates, rng, start, end, manager_id)
    build.fates = fates                      # what was planted, for tests and the run log
    return feed


def _statement(add, lines: list[dict], fates: dict[str, str], rng, start: date, end: date,
               manager_id: str) -> None:
    """Keyway's statement for the period, sent the next weekday. Two lines on invoices paid on
    time state a different amount than Oakline approved."""
    period_end = end - timedelta(days=STATEMENT_LAG_DAYS)
    sent = period_end + timedelta(days=1)
    while sent.weekday() >= 5:
        sent += timedelta(days=1)
    rows = sorted((dict(l) for l in lines if l["on"] <= period_end.isoformat()),
                  key=lambda l: (l["on"], l["invoice_id"]))
    if not rows or sent > end:
        return
    clean = [l for l in rows if fates.get(l["invoice_id"]) == "on_time"]
    for line, ratio in zip(rng.sample(clean, min(len(clean), len(STATEMENT_MISMATCHES))), STATEMENT_MISMATCHES):
        line["amount"] = _money(line["amount"] * ratio)
        fates[line["invoice_id"]] = "statement_amount"
    numbered = [{**l, "line": f"L{i}"} for i, l in enumerate(rows, 1)]
    text = "; ".join(f"{l['on']} {l['invoice_id']} {_usd(l['amount'])} PAID" for l in numbered)
    add(sent, Document(
        id=f"doc-stmt-keyway-live-{period_end}", kind=SourceKind.manager_statement, manager_id=manager_id,
        dated=period_end,
        text=f"Keyway Property Management statement {start} to {period_end}: {text}",
        data={"lines": numbered}))
