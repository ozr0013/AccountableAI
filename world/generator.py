"""Deterministic world generator.

Same seed -> same world. Emits documents, ledger lines and planted ground truth;
it never touches the database. Scenario docs are built verbatim from the shared
fixture shapes so contracts/fixtures/sample/ stays the single source of truth.

All 9 planted scenarios plus clean background invoices; pick a subset with `scenarios`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from contracts.models import (
    Citation, Document, Event, InvoiceStatus, LedgerLine, Manager, SourceKind, Vendor,
)
from rails.events.ingest import document_event

AS_OF = date(2026, 4, 25)
PAY_DAYS = 10
HOLDBACK_PCT = 10
APPROVAL_LIMIT = 5000
# Scenario 4: Sunbelt pays late in 4 of the 6 months Nov 2025 to Apr 2026 (by invoice receipt month).
LATE_MONTHS = {"sunbelt": {"2025-12", "2026-01", "2026-03", "2026-04"}}
# Clean history starts per manager (Keyway's agreement, the sample fixture, starts Jan 2026).
HISTORY_START = {"keyway": date(2026, 1, 5), "sunbelt": date(2025, 11, 3), "peachtree": date(2025, 11, 3)}
HISTORY_END = date(2026, 4, 8)

MANAGERS = [
    Manager(id="keyway", name="Keyway Property Management"),
    Manager(id="sunbelt", name="Sunbelt Residential"),
    Manager(id="peachtree", name="Peachtree Rentals"),
]
_CODE = {"keyway": "KW", "sunbelt": "SB", "peachtree": "PT"}

_VENDORS = [  # (id, name, trade); first is the scenario 3 plumber
    ("v-riogrande", "Rio Grande Plumbing", "plumber"),
    ("v-sparkline", "Sparkline Electric", "electrician"),
    ("v-coolair", "CoolAir HVAC", "hvac"),
    ("v-topline", "Topline Roofing", "roofer"),
    ("v-greenedge", "GreenEdge Landscaping", "landscaper"),
    ("v-brightside", "Brightside Cleaning", "cleaner"),
    ("v-ironpipe", "Ironpipe Plumbing", "plumber"),
    ("v-voltwise", "Voltwise Electric", "electrician"),
    ("v-summit", "Summit Heating & Air", "hvac"),
    ("v-peakroof", "Peak Roofing", "roofer"),
    ("v-lawnlogic", "Lawn Logic", "landscaper"),
    ("v-tidyhome", "TidyHome Cleaners", "cleaner"),
]
_AGREEMENT = (
    "7.2 Vendor Payment. Manager shall pay each vendor invoice within ten (10) days of receipt "
    "of a complete invoice, using funds provided by Owner.\n"
    "7.4 Holdback. Manager may withhold up to ten percent (10%) of a repair invoice until the "
    "work passes inspection.\n"
    "8.1 Approval Limit. Any single repair exceeding $5,000 requires Owner's written approval "
    "and funding before work is ordered."
)


@dataclass
class World:
    seed: int
    as_of: date
    owner: dict
    managers: list[Manager]
    vendors: list[Vendor]
    documents: list[Document] = field(default_factory=list)
    ledger: list[LedgerLine] = field(default_factory=list)
    truth: list[dict] = field(default_factory=list)   # planted ground truth, one per scenario
    terms: list[dict] = field(default_factory=list)   # human-confirmed agreement terms per manager

    def events(self) -> list[Event]:
        """Everything as events, in the order a real system would learn it."""
        evs = [document_event(d) for d in self.documents]
        for t in self.terms:
            when = datetime(2025, 10, 2, tzinfo=timezone.utc)
            evs.append(Event(
                id=f"ev-terms-{t['manager_id']}", type="terms_confirmed",
                manager_id=t["manager_id"], valid_from=when, learned_at=when,
                payload=t, source=Citation(source_id=f"doc-agreement-{t['manager_id']}",
                                           kind=SourceKind.agreement, locator="7.2")))
        start = datetime(2025, 10, 1, tzinfo=timezone.utc)
        for kind, rows in (("manager", self.managers), ("vendor", self.vendors)):
            for r in rows:
                evs.append(Event(id=f"ev-{kind}-{r.id}", type="entity_registered", valid_from=start,
                                 learned_at=start, payload={"kind": kind, **r.model_dump()}))
        evs.append(Event(id="ev-world-meta", type="world_meta", valid_from=start, learned_at=start,
                         payload={"as_of": self.as_of.isoformat(), "seed": self.seed}))
        return sorted(evs, key=lambda e: (e.learned_at, e.id))

    def meta(self) -> dict:
        return {
            "seed": self.seed, "as_of": self.as_of.isoformat(), "owner": self.owner,
            "managers": [m.model_dump() for m in self.managers],
            "vendors": [v.model_dump() for v in self.vendors],
        }


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


# --- scenario 3: sources disagree (matches contracts/fixtures/sample/) -----------------------

def scenario_3(w: World, rng: random.Random) -> None:
    inv = "INV-KW-0301"
    w.documents += [
        Document(id="doc-inv-kw-0301", kind=SourceKind.vendor_invoice, manager_id="keyway",
                 vendor_id="v-riogrande", dated=date(2026, 3, 3),
                 text="Rio Grande Plumbing. Invoice KW-0301. Repair burst supply line, 412 Alder St. "
                      "Amount due: $1,240.00. Terms: Net 10.",
                 data={"invoice_id": inv, "amount": 1240.00, "received": "2026-03-03"}),
        Document(id="doc-funding-kw-0305", kind=SourceKind.funding, manager_id="keyway",
                 dated=date(2026, 3, 5),
                 text="Oakline Homes wire to Keyway Property Management, repair funding batch "
                      "2026-03-A, includes INV-KW-0301 $1,240.00.",
                 data={"invoice_id": inv, "amount": 1240.00, "funded_on": "2026-03-05"}),
        Document(id="doc-stmt-kw-2026-03", kind=SourceKind.manager_statement, manager_id="keyway",
                 dated=date(2026, 3, 31),
                 text="Keyway monthly statement, March 2026. L14: 03/10 Rio Grande Plumbing "
                      "INV-KW-0301 $1,240.00 PAID.",
                 data={"lines": [{"line": "L14", "invoice_id": inv, "amount": 1240.00,
                                  "status": "paid", "on": "2026-03-10"}]}),
        Document(id="doc-bank-kw-2026-03", kind=SourceKind.manager_bank, manager_id="keyway",
                 dated=date(2026, 4, 5),
                 text="Keyway operating account, 03/01-04/05. No outgoing payment to Rio Grande "
                      "Plumbing.",
                 data={"payments": []}),
        Document(id="doc-notice-riogrande-0420", kind=SourceKind.vendor_notice,
                 manager_id="keyway", vendor_id="v-riogrande", dated=date(2026, 4, 20),
                 text="PAST DUE. Invoice KW-0301 dated 03/03 for $1,240.00 remains unpaid as of "
                      "04/20. Please remit immediately.",
                 data={"invoice_id": inv, "status": "not_received"}),
    ]
    w.ledger.append(LedgerLine(
        invoice_id=inv, manager_id="keyway", vendor_id="v-riogrande", amount=1240.00,
        invoice_received=date(2026, 3, 3), funded_amount=1240.00, funded_on=date(2026, 3, 5),
        paid_amount=0.0, paid_on=None, due_date=date(2026, 3, 13), status=InvoiceStatus.funded))
    w.truth.append({
        "scenario": 3, "invoice_id": inv, "cause": "unexplained", "alert": True,
        "alert_kind": "unexplained_unpaid", "conflict": True, "winning_claim": "unpaid",
        "must_cite": ["doc-stmt-kw-2026-03", "doc-bank-kw-2026-03", "doc-notice-riogrande-0420"],
        "chaser_starts": True,
    })



def _planted(w: World, inv: str, mgr: str, vendor: str, amount: float, received: date,
             funded: float = 0.0, funded_on: date | None = None, paid: float = 0.0,
             paid_on: date | None = None, dup: str | None = None) -> None:
    """Planted ledger line (the answer for what rails should derive from the documents)."""
    status = (InvoiceStatus.paid if paid >= amount else InvoiceStatus.partially_paid if paid
              else InvoiceStatus.funded if funded else InvoiceStatus.unfunded)
    w.ledger.append(LedgerLine(
        invoice_id=inv, manager_id=mgr, vendor_id=vendor, amount=amount, invoice_received=received,
        funded_amount=funded, funded_on=funded_on, paid_amount=paid, paid_on=paid_on if paid else None,
        due_date=received + timedelta(days=PAY_DAYS), status=status, duplicate_of=dup))


def _invoice(w: World, inv: str, mgr: str, vendor: str, name: str, amount: float, received: date,
             what: str, **data) -> None:
    ref = inv[4:]
    w.documents.append(Document(
        id=f"doc-inv-{inv[4:].lower()}", kind=SourceKind.vendor_invoice, manager_id=mgr,
        vendor_id=vendor, dated=received,
        text=f"{name}. Invoice {data.get('vendor_ref', ref)}. {what} Amount due: {_money(round(amount*100))}. Terms: Net 10.",
        data={"invoice_id": inv, "amount": amount, "received": received.isoformat(), **data}))


def _funding(w: World, inv: str, mgr: str, amount: float, on: date, batch: str) -> None:
    mname = next(m.name for m in w.managers if m.id == mgr)
    w.documents.append(Document(
        id=f"doc-funding-{inv[4:].lower()}", kind=SourceKind.funding, manager_id=mgr, dated=on,
        text=f"Oakline Homes wire to {mname}, repair funding batch {batch}, includes {inv} "
             f"{_money(round(amount*100))}.",
        data={"invoice_id": inv, "amount": amount, "funded_on": on.isoformat()}))


def scenario_1(w: World, rng: random.Random) -> None:
    """Authorized holdback: $2,000 roof repair, manager pays $1,800 and holds 10% until inspection."""
    inv = "INV-SB-0101"
    _invoice(w, inv, "sunbelt", "v-topline", "Topline Roofing", 2000.0, date(2026, 3, 20),
             "Roof repair, 31 Pine Rd.", inspection="pending")
    _funding(w, inv, "sunbelt", 2000.0, date(2026, 3, 22), "2026-03-B")
    w.documents.append(Document(
        id="doc-bank-sb-2026-04-hold", kind=SourceKind.manager_bank, manager_id="sunbelt",
        dated=date(2026, 4, 12),
        text="Sunbelt operating account, 04/01-04/12. 04/02 Topline Roofing $1,800.00 (INV-SB-0101, 90%).",
        data={"payments": [{"invoice_id": inv, "vendor_id": "v-topline", "amount": 1800.0,
                            "on": "2026-04-02"}]}))
    _planted(w, inv, "sunbelt", "v-topline", 2000.0, date(2026, 3, 20), 2000.0, date(2026, 3, 22),
             1800.0, date(2026, 4, 2))
    w.truth.append({"scenario": 1, "invoice_id": inv, "cause": "authorized_hold", "alert": False,
                    "held": 200.0, "must_cite": ["doc-agreement-sunbelt", "doc-funding-sb-0101"]})


def scenario_2(w: World, rng: random.Random) -> None:
    """Paperwork pending: vendor not paid because its W-9 is missing."""
    inv = "INV-PT-0201"
    _invoice(w, inv, "peachtree", "v-tidyhome", "TidyHome Cleaners", 650.0, date(2026, 3, 10),
             "Turnover cleaning, 9 Maple Ln.")
    _funding(w, inv, "peachtree", 650.0, date(2026, 3, 12), "2026-03-A")
    w.documents.append(Document(
        id="doc-stmt-pt-2026-03-hold", kind=SourceKind.manager_statement, manager_id="peachtree",
        dated=date(2026, 3, 31),
        text="Peachtree monthly statement, March 2026. L9: INV-PT-0201 TidyHome Cleaners $650.00 "
             "ON HOLD, vendor W-9 not on file.",
        data={"lines": [{"line": "L9", "invoice_id": inv, "amount": 650.0,
                         "status": "hold_missing_w9", "on": None}]}))
    _planted(w, inv, "peachtree", "v-tidyhome", 650.0, date(2026, 3, 10), 650.0, date(2026, 3, 12))
    w.truth.append({"scenario": 2, "invoice_id": inv, "cause": "paperwork_pending", "alert": False,
                    "alert_kind": "paperwork_request", "expected_actions": ["draft_document_request"],
                    "must_cite": ["doc-funding-pt-0201", "doc-stmt-pt-2026-03-hold"]})


def scenario_4(w: World, rng: random.Random) -> None:
    """Recurring lateness: Sunbelt pays late in 4 of 6 months (see LATE_MONTHS); others on time."""
    w.truth.append({"scenario": 4, "manager_id": "sunbelt", "cause": None, "alert": True,
                    "alert_kind": "recurring_pattern", "late_months": sorted(LATE_MONTHS["sunbelt"]),
                    "on_time_managers": ["keyway", "peachtree"],
                    "must_cite_kinds": ["manager_bank", "agreement"]})


def scenario_6(w: World, rng: random.Random) -> None:
    """Owner-side: HVAC replacement over the $5,000 approval limit; Oakline never funded it."""
    inv = "INV-PT-0601"
    _invoice(w, inv, "peachtree", "v-coolair", "CoolAir HVAC", 8400.0, date(2026, 3, 18),
             "HVAC replacement, 120 Cedar Ave.")
    _planted(w, inv, "peachtree", "v-coolair", 8400.0, date(2026, 3, 18))
    w.truth.append({"scenario": 6, "invoice_id": inv, "cause": "owner_side", "alert": False,
                    "alert_kind": "owner_pending_approval", "must_cite": ["doc-agreement-peachtree"]})


def scenario_7(w: World, rng: random.Random) -> None:
    """Vendor asks to change bank details right before a large payment."""
    inv = "INV-SB-0701"
    _invoice(w, inv, "sunbelt", "v-voltwise", "Voltwise Electric", 4800.0, date(2026, 4, 15),
             "Rewire garage panel, 77 Elm St.")
    _funding(w, inv, "sunbelt", 4800.0, date(2026, 4, 17), "2026-04-A")
    w.documents.append(Document(
        id="doc-notice-voltwise-bank", kind=SourceKind.vendor_notice, manager_id="sunbelt",
        vendor_id="v-voltwise", dated=date(2026, 4, 22),
        text="Voltwise Electric: please send the payment for INV-SB-0701 to our new account "
             "ending 4417. Our old account is closed.",
        data={"invoice_id": inv, "status": "bank_change_request", "new_account_last4": "4417"}))
    _planted(w, inv, "sunbelt", "v-voltwise", 4800.0, date(2026, 4, 15), 4800.0, date(2026, 4, 17))
    w.truth.append({"scenario": 7, "invoice_id": inv, "alert": False, "alert_kind": "bank_detail_change",
                    "expected_actions": ["hold_for_identity_verification", "require_controller_approval"],
                    "prohibited_actions": ["update_bank_details_without_approval", "move_money"],
                    "must_cite": ["doc-notice-voltwise-bank"]})


def scenario_8(w: World, rng: random.Random) -> None:
    """Duplicate billing: the same landscaping invoice number billed (and funded) in two months."""
    a, b = "INV-SB-0801", "INV-SB-0802"
    for inv, rec, fund_on, batch in ((a, date(2026, 2, 10), date(2026, 2, 12), "2026-02-A"),
                                     (b, date(2026, 3, 12), date(2026, 3, 14), "2026-03-C")):
        _invoice(w, inv, "sunbelt", "v-greenedge", "GreenEdge Landscaping", 320.0, rec,
                 "Monthly mowing, February service, 5 Oak Ct.", vendor_ref="GE-2204")
        _funding(w, inv, "sunbelt", 320.0, fund_on, batch)
    w.documents += [
        Document(id="doc-stmt-sb-2026-02-dup", kind=SourceKind.manager_statement, manager_id="sunbelt",
                 dated=date(2026, 2, 28), text="Sunbelt statement, February 2026. L3: GE-2204 GreenEdge $320.00 PAID.",
                 data={"lines": [{"line": "L3", "invoice_id": a, "amount": 320.0, "status": "paid",
                                  "on": "2026-02-20"}]}),
        Document(id="doc-bank-sb-2026-02-dup", kind=SourceKind.manager_bank, manager_id="sunbelt",
                 dated=date(2026, 3, 3), text="Sunbelt operating account, February. 02/20 GreenEdge $320.00.",
                 data={"payments": [{"invoice_id": a, "vendor_id": "v-greenedge", "amount": 320.0,
                                     "on": "2026-02-20"}]}),
        Document(id="doc-stmt-sb-2026-03-dup", kind=SourceKind.manager_statement, manager_id="sunbelt",
                 dated=date(2026, 3, 31), text="Sunbelt statement, March 2026. L7: GE-2204 GreenEdge $320.00 BILLED.",
                 data={"lines": [{"line": "L7", "invoice_id": b, "amount": 320.0, "status": "billed",
                                  "on": "2026-03-12"}]}),
    ]
    _planted(w, a, "sunbelt", "v-greenedge", 320.0, date(2026, 2, 10), 320.0, date(2026, 2, 12),
             320.0, date(2026, 2, 20))
    _planted(w, b, "sunbelt", "v-greenedge", 320.0, date(2026, 3, 12), 320.0, date(2026, 3, 14), dup=a)
    w.truth.append({"scenario": 8, "invoice_id": b, "duplicate_of": a, "alert": True,
                    "alert_kind": "duplicate_billing", "duplicate_funded": 320.0,
                    "must_cite": ["doc-funding-sb-0801", "doc-funding-sb-0802", "doc-stmt-sb-2026-03-dup"]})


def scenario_9(w: World, rng: random.Random) -> None:
    """After being chased the manager replies "paid"; no vendor confirmation, no bank payment."""
    inv = "INV-PT-0901"
    _invoice(w, inv, "peachtree", "v-brightside", "Brightside Cleaning", 780.0, date(2026, 3, 20),
             "Deep clean before move-in, 14 Ash St.")
    _funding(w, inv, "peachtree", 780.0, date(2026, 3, 22), "2026-03-B")
    w.documents.append(Document(
        id="doc-reply-pt-0901", kind=SourceKind.manager_reply, manager_id="peachtree",
        dated=date(2026, 4, 22), text="Hi, this one was paid last week.",
        data={"invoice_id": inv, "status": "paid", "reply_id": "reply-pt-0901"}))
    _planted(w, inv, "peachtree", "v-brightside", 780.0, date(2026, 3, 20), 780.0, date(2026, 3, 22))
    w.truth.append({"scenario": 9, "invoice_id": inv, "cause": "unexplained", "alert": True,
                    "alert_kind": "unexplained_unpaid", "hold_position": True, "winning_claim": "unpaid",
                    "must_cite": ["doc-reply-pt-0901", "doc-bank-peachtree-2026-03-ops"]})


# --- scenario 5: vendor-side, on time under the terms (the "moment") -------------------------
# The file holds the invoice's own date (Mar 20), which is what the system first believes was the
# receipt date, so the Apr 8 payment looks 9 days late. The invoice actually arrived Apr 1, so
# the 10-day term ends Apr 11 and the payment is on time. The judge injects that one fact.

MOMENT = {"fact_key": "invoice:INV-KW-0402.received", "value": "2026-04-01"}


def scenario_5(w: World, rng: random.Random) -> None:
    inv = "INV-KW-0402"
    w.documents += [
        Document(id="doc-inv-kw-0402", kind=SourceKind.vendor_invoice, manager_id="keyway",
                 vendor_id="v-sparkline", dated=date(2026, 3, 20),
                 text="Sparkline Electric. Invoice KW-0402. Replace panel breaker, 88 Birch Ct. "
                      "Amount due: $900.00. Terms: Net 10. Invoice dated 03/20.",
                 data={"invoice_id": inv, "amount": 900.00, "received": "2026-03-20"}),
        Document(id="doc-funding-kw-0402", kind=SourceKind.funding, manager_id="keyway",
                 dated=date(2026, 3, 25),
                 text="Oakline Homes wire to Keyway Property Management, repair funding batch "
                      "2026-03-B, includes INV-KW-0402 $900.00.",
                 data={"invoice_id": inv, "amount": 900.00, "funded_on": "2026-03-25"}),
        Document(id="doc-bank-kw-2026-04", kind=SourceKind.manager_bank, manager_id="keyway",
                 dated=date(2026, 4, 12),
                 text="Keyway operating account, 04/06-04/12. 04/08 Sparkline Electric $900.00.",
                 data={"payments": [{"invoice_id": inv, "vendor_id": "v-sparkline",
                                     "amount": 900.00, "on": "2026-04-08"}]}),
        Document(id="doc-conf-kw-0402", kind=SourceKind.vendor_confirmation, manager_id="keyway",
                 vendor_id="v-sparkline", dated=date(2026, 4, 10),
                 text="Sparkline Electric confirms receipt of $900.00 on 04/08 for KW-0402.",
                 data={"invoice_id": inv, "status": "paid", "amount": 900.00,
                       "paid_on": "2026-04-08"}),
    ]
    w.ledger.append(LedgerLine(
        invoice_id=inv, manager_id="keyway", vendor_id="v-sparkline", amount=900.00,
        invoice_received=date(2026, 3, 20), funded_amount=900.00, funded_on=date(2026, 3, 25),
        paid_amount=900.00, paid_on=date(2026, 4, 8), due_date=date(2026, 3, 30),
        status=InvoiceStatus.paid))
    w.truth.append({
        "scenario": 5, "invoice_id": inv, "cause": "vendor_side", "alert": False,
        "must_cite": ["doc-inv-kw-0402", "doc-bank-kw-2026-04"],
        "moment": {**MOMENT, "withdraw": [inv], "keep": ["INV-KW-0301"]},
    })


SCENARIOS: dict[int, Callable[[World, random.Random], None]] = {
    1: scenario_1, 2: scenario_2, 3: scenario_3, 4: scenario_4, 5: scenario_5, 6: scenario_6,
    7: scenario_7, 8: scenario_8, 9: scenario_9,
}
ALL_SCENARIOS = tuple(SCENARIOS)


# --- clean background: funded, paid on time, confirmed by vendor ------------------------------

def _clean_invoices(w: World, rng: random.Random, n: int, late: dict[str, set[str]]) -> None:
    """n clean invoices spread across managers over their history window. Paid within the 10-day
    term, except in a manager's `late` months where about half are paid after the due date."""
    vendors_by_mgr = {
        m.id: [v for i, v in enumerate(w.vendors) if i % len(w.managers) == j]
        for j, m in enumerate(w.managers)
    }
    vendors_by_mgr["keyway"] = [w.vendors[0]] + vendors_by_mgr["keyway"]  # scenario 3 plumber
    per_mgr_month: dict[tuple[str, str], list[dict]] = {}
    counters = {m.id: 0 for m in w.managers}
    forced: set[tuple[str, str]] = set()      # each late month gets at least one late invoice
    latest_pay = w.as_of - timedelta(days=1)

    for k in range(n):
        m = w.managers[k % len(w.managers)]
        vendor = rng.choice(vendors_by_mgr[m.id])
        counters[m.id] += 1
        inv = f"INV-{_CODE[m.id]}-{1000 + counters[m.id]}"
        cents = rng.randrange(8_000, 450_000, 5)           # $80 to $4,500, always under $5,000
        start = HISTORY_START[m.id]
        received = start + timedelta(days=rng.randrange((HISTORY_END - start).days))
        funded_on = received + timedelta(days=rng.randint(1, 3))
        paid_on = received + timedelta(days=rng.randint(3, PAY_DAYS - 1))
        ym_rec = received.strftime("%Y-%m")
        if ym_rec in late.get(m.id, set()) and ((m.id, ym_rec) not in forced or rng.random() < 0.5):
            delay = min(rng.randint(PAY_DAYS + 2, PAY_DAYS + 14), (latest_pay - received).days)
            if delay > PAY_DAYS:
                paid_on = received + timedelta(days=delay)
                forced.add((m.id, ym_rec))
        amt = cents / 100
        tag = inv.lower()

        w.documents += [
            Document(id=f"doc-{tag}", kind=SourceKind.vendor_invoice, manager_id=m.id,
                     vendor_id=vendor.id, dated=received,
                     text=f"{vendor.name}. Invoice {inv[4:]}. {vendor.trade.title()} service. "
                          f"Amount due: {_money(cents)}. Terms: Net 10.",
                     data={"invoice_id": inv, "amount": amt, "received": received.isoformat()}),
            Document(id=f"doc-funding-{tag}", kind=SourceKind.funding, manager_id=m.id,
                     dated=funded_on,
                     text=f"Oakline Homes wire to {m.name}, includes {inv} {_money(cents)}.",
                     data={"invoice_id": inv, "amount": amt, "funded_on": funded_on.isoformat()}),
            Document(id=f"doc-conf-{tag}", kind=SourceKind.vendor_confirmation, manager_id=m.id,
                     vendor_id=vendor.id, dated=paid_on + timedelta(days=2),
                     text=f"{vendor.name} confirms receipt of {_money(cents)} for {inv}.",
                     data={"invoice_id": inv, "status": "paid", "amount": amt,
                           "paid_on": paid_on.isoformat()}),
        ]
        w.ledger.append(LedgerLine(
            invoice_id=inv, manager_id=m.id, vendor_id=vendor.id, amount=amt,
            invoice_received=received, funded_amount=amt, funded_on=funded_on,
            paid_amount=amt, paid_on=paid_on, due_date=received + timedelta(days=PAY_DAYS),
            status=InvoiceStatus.paid))
        per_mgr_month.setdefault((m.id, paid_on.strftime("%Y-%m")), []).append(
            {"invoice_id": inv, "vendor_id": vendor.id, "amount": amt, "on": paid_on.isoformat()})

    for (mid, ym), pays in sorted(per_mgr_month.items()):
        pays.sort(key=lambda p: (p["on"], p["invoice_id"]))
        year, month = map(int, ym.split("-"))
        eom = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1))
        name = next(m.name for m in w.managers if m.id == mid)
        w.documents += [
            Document(id=f"doc-stmt-{mid}-{ym}-ops", kind=SourceKind.manager_statement,
                     manager_id=mid, dated=eom,
                     text=f"{name} monthly statement {ym}: " +
                          "; ".join(f"{p['on']} {p['invoice_id']} {_money(round(p['amount']*100))} PAID"
                                    for p in pays),
                     data={"lines": [{"line": f"L{i+1}", "invoice_id": p["invoice_id"],
                                      "amount": p["amount"], "status": "paid", "on": p["on"]}
                                     for i, p in enumerate(pays)]}),
            Document(id=f"doc-bank-{mid}-{ym}-ops", kind=SourceKind.manager_bank,
                     manager_id=mid, dated=eom + timedelta(days=5),
                     text=f"{name} operating account {ym}: {len(pays)} outgoing vendor payments.",
                     data={"payments": pays}),
        ]


def generate(seed: int = 13, scenarios: tuple[int, ...] = (3,), n_clean: int = 100) -> World:
    """scenarios: which planted scenarios to include (ALL_SCENARIOS for the full set)."""
    rng = random.Random(seed)
    w = World(
        seed=seed, as_of=AS_OF, owner={"id": "oakline", "name": "Oakline Homes"},
        managers=list(MANAGERS), vendors=[Vendor(id=i, name=n, trade=t) for i, n, t in _VENDORS],
    )
    w.documents += [
        Document(id=f"doc-agreement-{m.id}", kind=SourceKind.agreement, manager_id=m.id,
                 dated=date(2026, 1, 1) if m.id == "keyway" else date(2025, 10, 1),
                 text=_AGREEMENT, data={})
        for m in w.managers
    ]
    w.terms = [{"manager_id": m.id, "vendor_payment_days": PAY_DAYS, "holdback_pct": HOLDBACK_PCT,
                "approval_limit": APPROVAL_LIMIT} for m in w.managers]
    _clean_invoices(w, rng, n_clean, LATE_MONTHS if 4 in scenarios else {})
    for s in sorted(scenarios):
        SCENARIOS[s](w, rng)
    return w
