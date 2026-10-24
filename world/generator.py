"""Deterministic world generator: one parent (Oakline Homes), one middleman (Keyway).

Same seed -> same world. Emits documents, ledger lines and planted ground truth;
it never touches the database. Scenario docs are built verbatim from the shared
fixture shapes so contracts/fixtures/sample/ stays the single source of truth.

All 9 planted scenarios, the judge moment, and clean background invoices; pick a subset with
`scenarios`. Nothing is dated after WORLD_TODAY, the demo date.
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

WORLD_TODAY = date(2026, 4, 22)       # the demo date: every generated event is on or before it
AS_OF = WORLD_TODAY                   # kept for older imports
PAY_DAYS = 10
HOLDBACK_PCT = 10
APPROVAL_LIMIT = 5000
MANAGER = Manager(id="keyway", name="Keyway Property Management")
MANAGERS = [MANAGER]
# Scenario 4: Keyway pays past the agreement's payment window in 3 of 4 months (Jan to Apr 2026,
# by invoice receipt month). April is on time.
LATE_MONTHS = {"keyway": {"2026-01", "2026-02", "2026-03"}}
LATE_SHARE = 0.12                     # share of invoices paid late in a late month (min. one)
HISTORY_START = date(2026, 1, 5)      # Keyway's agreement (the sample fixture) starts Jan 2026
HISTORY_END = date(2026, 4, 8)

_VENDORS = [  # (id, name, trade). Every vendor works for Keyway.
    ("v-riogrande", "Rio Grande Plumbing", "plumber"),       # scenario 3
    ("v-sparkline", "Sparkline Electric", "electrician"),    # the moment
    ("v-amperpro", "AmperPro Electric", "electrician"),      # scenario 5
    ("v-topline", "Topline Roofing", "roofer"),              # scenario 1
    ("v-tidyhome", "TidyHome Cleaners", "cleaner"),          # scenario 2
    ("v-coolair", "CoolAir HVAC", "hvac"),                   # scenario 6
    ("v-summit", "Summit Heating & Air", "hvac"),            # scenario 7
    ("v-greenedge", "GreenEdge Landscaping", "landscaper"),  # scenario 8
    ("v-brightside", "Brightside Cleaning", "cleaner"),      # scenario 9
    ("v-ironpipe", "Ironpipe Plumbing", "plumber"),
    ("v-peakroof", "Peak Roofing", "roofer"),
    ("v-lawnlogic", "Lawn Logic", "landscaper"),
    ("v-clearflow", "ClearFlow Drains", "plumber"),
    ("v-breezeway", "Breezeway HVAC", "hvac"),
    ("v-shingle", "Shingle Masters", "roofer"),
    ("v-fresh", "Fresh Start Cleaning", "cleaner"),
    ("v-yardpro", "YardPro Landscaping", "landscaper"),
    ("v-handy", "Handy Crew Repairs", "handyman"),
    ("v-paintpro", "PaintPro Painting", "painter"),
    ("v-flowfix", "FlowFix Plumbing", "plumber"),
]
# Background invoices never go to an electrician, so "the electrician" with an open invoice is
# always the moment's Sparkline invoice.
_CLEAN_TRADES_EXCLUDED = {"electrician"}
_AGREEMENT = (
    "7.2 Vendor Payment. Manager shall pay each vendor invoice within ten (10) days of receipt "
    "of a complete invoice, using funds provided by Owner.\n"
    "7.4 Holdback. Manager may withhold up to ten percent (10%) of a repair invoice until the "
    "work passes inspection.\n"
    "8.1 Approval Limit. Any single repair exceeding $5,000 requires Owner's written approval "
    "and funding before work is ordered."
)

# The judge moment. Keyway's March statement implies the Sparkline invoice arrived Mar 28, so it
# looks past due (due Apr 7). It truly arrived Apr 15 (due Apr 25). The judge injects that fact.
MOMENT = {"fact_key": "invoice:INV-KW-0402.received", "value": "2026-04-15"}
MOMENT_RECORDED = "ev-fact-kw-0402-received"      # the Mar 28 fact the correction supersedes


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
    facts: list[Event] = field(default_factory=list)  # facts read off documents (fact_recorded)
    moment: dict | None = None                         # the judge moment, when planted
    recipe: dict = field(default_factory=dict)         # how it was generated (scenarios, n_clean)

    def events(self) -> list[Event]:
        """Everything as events, in the order a real system would learn it."""
        evs = [document_event(d) for d in self.documents] + list(self.facts)
        for t in self.terms:
            when = datetime(2026, 1, 2, tzinfo=timezone.utc)
            evs.append(Event(
                id=f"ev-terms-{t['manager_id']}", type="terms_confirmed",
                manager_id=t["manager_id"], valid_from=when, learned_at=when,
                payload=t, source=Citation(source_id=f"doc-agreement-{t['manager_id']}",
                                           kind=SourceKind.agreement, locator="7.2")))
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for kind, rows in (("manager", self.managers), ("vendor", self.vendors)):
            for r in rows:
                evs.append(Event(id=f"ev-{kind}-{r.id}", type="entity_registered", valid_from=start,
                                 learned_at=start, payload={"kind": kind, **r.model_dump()}))
        evs.append(Event(id="ev-world-meta", type="world_meta", valid_from=start, learned_at=start,
                         payload={"as_of": self.as_of.isoformat(), "seed": self.seed, **self.recipe}))
        return sorted(evs, key=lambda e: (e.learned_at, e.id))

    def meta(self) -> dict:
        return {
            "seed": self.seed, "as_of": self.as_of.isoformat(), "owner": self.owner,
            "managers": [m.model_dump() for m in self.managers],
            "vendors": [v.model_dump() for v in self.vendors],
        }


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _vendor_name(vendor_id: str) -> str:
    return next(n for i, n, _ in _VENDORS if i == vendor_id)


def _planted(w: World, inv: str, vendor: str, amount: float, received: date | None,
             funded: float = 0.0, funded_on: date | None = None, paid: float = 0.0,
             paid_on: date | None = None, dup: str | None = None) -> None:
    """Planted ledger line (the answer for what rails should derive from the documents)."""
    status = (InvoiceStatus.paid if paid >= amount else InvoiceStatus.partially_paid if paid
              else InvoiceStatus.funded if funded else InvoiceStatus.unfunded)
    w.ledger.append(LedgerLine(
        invoice_id=inv, manager_id="keyway", vendor_id=vendor, amount=amount,
        invoice_received=received, funded_amount=funded, funded_on=funded_on, paid_amount=paid,
        paid_on=paid_on if paid else None,
        due_date=received + timedelta(days=PAY_DAYS) if received else None, status=status,
        duplicate_of=dup))


def _invoice(w: World, inv: str, vendor: str, amount: float, dated: date, what: str,
             received: date | None = None, **data) -> None:
    """Vendor invoice. `received` defaults to the invoice date; pass dated only (received=None
    and no_receipt=True) when the receipt date comes from another source."""
    d = {"invoice_id": inv, "amount": amount, **data}
    if not d.pop("no_receipt", False):
        d["received"] = (received or dated).isoformat()
    w.documents.append(Document(
        id=f"doc-inv-{inv[4:].lower()}", kind=SourceKind.vendor_invoice, manager_id="keyway",
        vendor_id=vendor, dated=dated,
        text=f"{_vendor_name(vendor)}. Invoice {data.get('vendor_ref', inv[4:])}. {what} "
             f"Amount due: {_money(round(amount * 100))}. Terms: Net 10.",
        data=d))


def _funding(w: World, inv: str, amount: float, on: date, batch: str) -> None:
    w.documents.append(Document(
        id=f"doc-funding-{inv[4:].lower()}", kind=SourceKind.funding, manager_id="keyway", dated=on,
        text=f"Oakline Homes wire to Keyway Property Management, repair funding batch {batch}, "
             f"includes {inv} {_money(round(amount * 100))}.",
        data={"invoice_id": inv, "amount": amount, "funded_on": on.isoformat()}))


def _bank(w: World, doc_id: str, dated: date, text: str, *payments: tuple[str, str, float, date]):
    w.documents.append(Document(
        id=doc_id, kind=SourceKind.manager_bank, manager_id="keyway", dated=dated, text=text,
        data={"payments": [{"invoice_id": i, "vendor_id": v, "amount": a, "on": on.isoformat()}
                           for i, v, a, on in payments]}))


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
    _planted(w, inv, "v-riogrande", 1240.00, date(2026, 3, 3), 1240.00, date(2026, 3, 5))
    w.truth.append({
        "scenario": 3, "invoice_id": inv, "cause": "unexplained", "alert": True,
        "alert_kind": "unexplained_unpaid", "conflict": True, "winning_claim": "unpaid",
        "must_cite": ["doc-stmt-kw-2026-03", "doc-bank-kw-2026-03", "doc-notice-riogrande-0420"],
        "chaser_starts": True,
    })


def scenario_1(w: World, rng: random.Random) -> None:
    """Authorized holdback: $2,000 roof repair, $1,800 paid on time, 10% held until inspection."""
    inv = "INV-KW-0101"
    _invoice(w, inv, "v-topline", 2000.0, date(2026, 3, 20), "Roof repair, 31 Pine Rd.",
             inspection="pending")
    _funding(w, inv, 2000.0, date(2026, 3, 22), "2026-03-B")
    _bank(w, "doc-bank-kw-2026-03-hold", date(2026, 3, 31),
          "Keyway operating account, 03/20-03/31. 03/27 Topline Roofing $1,800.00 "
          "(INV-KW-0101, 90%; 10% held until inspection).",
          (inv, "v-topline", 1800.0, date(2026, 3, 27)))
    _planted(w, inv, "v-topline", 2000.0, date(2026, 3, 20), 2000.0, date(2026, 3, 22),
             1800.0, date(2026, 3, 27))
    w.truth.append({"scenario": 1, "invoice_id": inv, "cause": "authorized_hold", "alert": False,
                    "held": 200.0, "must_cite": ["doc-agreement-keyway", "doc-funding-kw-0101"]})


def scenario_2(w: World, rng: random.Random) -> None:
    """Paperwork pending: vendor not paid because its W-9 is missing (past due on Apr 22)."""
    inv = "INV-KW-0201"
    _invoice(w, inv, "v-tidyhome", 650.0, date(2026, 3, 30), "Turnover cleaning, 9 Maple Ln.")
    _funding(w, inv, 650.0, date(2026, 4, 1), "2026-04-A")
    w.documents.append(Document(
        id="doc-stmt-kw-2026-04-hold", kind=SourceKind.manager_statement, manager_id="keyway",
        dated=date(2026, 4, 15),
        text="Keyway interim statement, April 2026. L9: INV-KW-0201 TidyHome Cleaners $650.00 "
             "ON HOLD, vendor W-9 not on file.",
        data={"lines": [{"line": "L9", "invoice_id": inv, "amount": 650.0,
                         "status": "hold_missing_w9", "on": None}]}))
    _planted(w, inv, "v-tidyhome", 650.0, date(2026, 3, 30), 650.0, date(2026, 4, 1))
    w.truth.append({"scenario": 2, "invoice_id": inv, "cause": "paperwork_pending", "alert": False,
                    "alert_kind": "paperwork_request", "expected_actions": ["draft_document_request"],
                    "must_cite": ["doc-funding-kw-0201", "doc-stmt-kw-2026-04-hold"]})


def scenario_4(w: World, rng: random.Random) -> None:
    """Recurring lateness against the agreement's own payment window (see LATE_MONTHS)."""
    w.truth.append({"scenario": 4, "manager_id": "keyway", "cause": None, "alert": True,
                    "alert_kind": "recurring_pattern", "payment_window_days": PAY_DAYS,
                    "months": ["2026-01", "2026-02", "2026-03", "2026-04"],
                    "late_months": sorted(LATE_MONTHS["keyway"]),
                    "must_cite_kinds": ["manager_bank", "agreement"]})


def scenario_5(w: World, rng: random.Random) -> None:
    """Vendor-side, on time under the terms: AmperPro invoice arrived Apr 1, paid Apr 8."""
    inv = "INV-KW-0501"
    _invoice(w, inv, "v-amperpro", 640.0, date(2026, 4, 1), "Replace GFCI outlets, 18 Spruce Way.")
    _funding(w, inv, 640.0, date(2026, 4, 2), "2026-04-A")
    _bank(w, "doc-bank-kw-2026-04-s5", date(2026, 4, 12),
          "Keyway operating account, 04/06-04/12. 04/08 AmperPro Electric $640.00.",
          (inv, "v-amperpro", 640.0, date(2026, 4, 8)))
    w.documents.append(Document(
        id="doc-conf-kw-0501", kind=SourceKind.vendor_confirmation, manager_id="keyway",
        vendor_id="v-amperpro", dated=date(2026, 4, 10),
        text="AmperPro Electric confirms receipt of $640.00 on 04/08 for KW-0501.",
        data={"invoice_id": inv, "status": "paid", "amount": 640.0, "paid_on": "2026-04-08"}))
    _planted(w, inv, "v-amperpro", 640.0, date(2026, 4, 1), 640.0, date(2026, 4, 2),
             640.0, date(2026, 4, 8))
    w.truth.append({"scenario": 5, "invoice_id": inv, "cause": "vendor_side", "alert": False,
                    "must_cite": ["doc-inv-kw-0501", "doc-bank-kw-2026-04-s5"]})


def scenario_6(w: World, rng: random.Random) -> None:
    """Owner-side: HVAC replacement over the $5,000 approval limit; Oakline never funded it."""
    inv = "INV-KW-0601"
    _invoice(w, inv, "v-coolair", 8400.0, date(2026, 3, 18), "HVAC replacement, 120 Cedar Ave.")
    _planted(w, inv, "v-coolair", 8400.0, date(2026, 3, 18))
    w.truth.append({"scenario": 6, "invoice_id": inv, "cause": "owner_side", "alert": False,
                    "alert_kind": "owner_pending_approval", "must_cite": ["doc-agreement-keyway"]})


def scenario_7(w: World, rng: random.Random) -> None:
    """Vendor asks to change bank details right before a large payment (not yet due)."""
    inv = "INV-KW-0701"
    _invoice(w, inv, "v-summit", 4800.0, date(2026, 4, 14), "Furnace replacement, 77 Elm St.")
    _funding(w, inv, 4800.0, date(2026, 4, 16), "2026-04-B")
    w.documents.append(Document(
        id="doc-notice-summit-bank", kind=SourceKind.vendor_notice, manager_id="keyway",
        vendor_id="v-summit", dated=date(2026, 4, 20),
        text="Summit Heating & Air: please send the payment for INV-KW-0701 to our new account "
             "ending 4417. Our old account is closed.",
        data={"invoice_id": inv, "status": "bank_change_request", "new_account_last4": "4417"}))
    _planted(w, inv, "v-summit", 4800.0, date(2026, 4, 14), 4800.0, date(2026, 4, 16))
    w.truth.append({"scenario": 7, "invoice_id": inv, "alert": False, "alert_kind": "bank_detail_change",
                    "expected_actions": ["hold_for_identity_verification", "require_controller_approval"],
                    "prohibited_actions": ["update_bank_details_without_approval", "move_money"],
                    "must_cite": ["doc-notice-summit-bank"]})


def scenario_8(w: World, rng: random.Random) -> None:
    """Duplicate billing: the same landscaping invoice number billed (and funded) in two months."""
    a, b = "INV-KW-0801", "INV-KW-0802"
    for inv, rec, fund_on, batch in ((a, date(2026, 2, 10), date(2026, 2, 12), "2026-02-A"),
                                     (b, date(2026, 3, 12), date(2026, 3, 14), "2026-03-C")):
        _invoice(w, inv, "v-greenedge", 320.0, rec,
                 "Monthly mowing, February service, 5 Oak Ct.", vendor_ref="GE-2204")
        _funding(w, inv, 320.0, fund_on, batch)
    w.documents += [
        Document(id="doc-stmt-kw-2026-02-dup", kind=SourceKind.manager_statement, manager_id="keyway",
                 dated=date(2026, 2, 28), text="Keyway statement, February 2026. L3: GE-2204 GreenEdge $320.00 PAID.",
                 data={"lines": [{"line": "L3", "invoice_id": a, "amount": 320.0, "status": "paid",
                                  "on": "2026-02-20"}]}),
        Document(id="doc-stmt-kw-2026-03-dup", kind=SourceKind.manager_statement, manager_id="keyway",
                 dated=date(2026, 3, 31), text="Keyway statement, March 2026. L7: GE-2204 GreenEdge $320.00 BILLED.",
                 data={"lines": [{"line": "L7", "invoice_id": b, "amount": 320.0, "status": "billed",
                                  "on": "2026-03-12"}]}),
    ]
    _bank(w, "doc-bank-kw-2026-02-dup", date(2026, 3, 3),
          "Keyway operating account, February. 02/20 GreenEdge $320.00.",
          (a, "v-greenedge", 320.0, date(2026, 2, 20)))
    _planted(w, a, "v-greenedge", 320.0, date(2026, 2, 10), 320.0, date(2026, 2, 12),
             320.0, date(2026, 2, 20))
    _planted(w, b, "v-greenedge", 320.0, date(2026, 3, 12), 320.0, date(2026, 3, 14), dup=a)
    w.truth.append({"scenario": 8, "invoice_id": b, "duplicate_of": a, "alert": True,
                    "alert_kind": "duplicate_billing", "duplicate_funded": 320.0,
                    "must_cite": ["doc-funding-kw-0801", "doc-funding-kw-0802", "doc-stmt-kw-2026-03-dup"]})


def scenario_9(w: World, rng: random.Random) -> None:
    """After being chased the manager replies "paid"; no vendor confirmation, no bank payment."""
    inv = "INV-KW-0901"
    _invoice(w, inv, "v-brightside", 1000.0, date(2026, 3, 9), "Deep clean before move-in, 14 Ash St.")
    _funding(w, inv, 1000.0, date(2026, 3, 11), "2026-03-A")
    w.documents.append(Document(
        id="doc-reply-kw-0901", kind=SourceKind.manager_reply, manager_id="keyway",
        dated=date(2026, 4, 21), text="Hi, the Brightside one was paid last week.",
        data={"invoice_id": inv, "status": "paid", "reply_id": "reply-kw-0901"}))
    _bank(w, "doc-bank-kw-2026-04-0901", date(2026, 4, 21),
          "Keyway operating account, 03/09-04/21. No outgoing payment to Brightside Cleaning.")
    _planted(w, inv, "v-brightside", 1000.0, date(2026, 3, 9), 1000.0, date(2026, 3, 11))
    w.truth.append({"scenario": 9, "invoice_id": inv, "cause": "unexplained", "alert": True,
                    "alert_kind": "unexplained_unpaid", "hold_position": True, "winning_claim": "unpaid",
                    "must_cite": ["doc-reply-kw-0901", "doc-bank-kw-2026-04-0901"]})


def plant_moment(w: World) -> None:
    """The moment's electrician (Sparkline): funded in March; the March statement implies the
    invoice arrived Mar 28, so it looks past due (due Apr 7). Truly received Apr 15 (due Apr 25).
    With scenarios 3 and 9 that makes three unexplained items: $1,240 + $1,000 + $900 = $3,140."""
    inv = "INV-KW-0402"
    _invoice(w, inv, "v-sparkline", 900.0, date(2026, 3, 26),
             "Replace panel breaker, 88 Birch Ct. Work completed 03/26.", no_receipt=True)
    _funding(w, inv, 900.0, date(2026, 3, 27), "2026-03-B")
    stmt = Document(
        id="doc-stmt-kw-2026-03-elec", kind=SourceKind.manager_statement, manager_id="keyway",
        dated=date(2026, 3, 31),
        text="Keyway monthly statement, March 2026. L18: Sparkline Electric INV-KW-0402 $900.00, "
             "invoice received 03/28, payment scheduled.",
        data={"lines": [{"line": "L18", "invoice_id": inv, "amount": 900.0, "status": "scheduled",
                         "on": None, "received": "2026-03-28"}]})
    w.documents.append(stmt)
    w.facts.append(Event(
        id=MOMENT_RECORDED, type="fact_recorded", manager_id="keyway", invoice_id=inv,
        valid_from=datetime(2026, 3, 28, tzinfo=timezone.utc),
        learned_at=datetime(2026, 3, 31, tzinfo=timezone.utc),
        payload={"fact_key": MOMENT["fact_key"], "value": "2026-03-28"},
        source=Citation(source_id=stmt.id, kind=SourceKind.manager_statement, locator="L18")))
    _planted(w, inv, "v-sparkline", 900.0, date(2026, 3, 28), 900.0, date(2026, 3, 27))
    w.moment = {
        "invoice_id": inv, "vendor_id": "v-sparkline", "amount": 900.0,
        "fact_key": MOMENT["fact_key"], "supersedes": MOMENT_RECORDED,
        "recorded": {"received": "2026-03-28", "due": "2026-04-07", "source": stmt.id},
        "corrected": {"received": MOMENT["value"], "due": "2026-04-25"},
        "unexplained_before": ["INV-KW-0301", "INV-KW-0402", "INV-KW-0901"],
        "unexplained_total_before": 3140.0,
        "withdraw": [inv], "keep": ["INV-KW-0301", "INV-KW-0901"],
        "unexplained_total_after": 2240.0,
    }


SCENARIOS: dict[int, Callable[[World, random.Random], None]] = {
    1: scenario_1, 2: scenario_2, 3: scenario_3, 4: scenario_4, 5: scenario_5, 6: scenario_6,
    7: scenario_7, 8: scenario_8, 9: scenario_9,
}
ALL_SCENARIOS = tuple(SCENARIOS)


# --- clean background: funded, paid, confirmed by vendor ----------------------------------

def _clean_invoices(w: World, rng: random.Random, n: int, late: set[str]) -> None:
    """n clean Keyway invoices received Jan 5 to Apr 8. Paid within the 10-day term, except in
    the `late` months, where a few are paid past it (at least one per late month)."""
    pool = [v for v in w.vendors if v.trade not in _CLEAN_TRADES_EXCLUDED]
    by_month: dict[str, list[dict]] = {}
    forced: set[str] = set()
    latest_pay = w.as_of - timedelta(days=1)
    span = (HISTORY_END - HISTORY_START).days

    for k in range(n):
        vendor = rng.choice(pool)
        inv = f"INV-KW-{1001 + k}"
        cents = rng.randrange(8_000, 450_000, 5)           # $80 to $4,500, always under $5,000
        received = HISTORY_START + timedelta(days=rng.randrange(span))
        funded_on = received + timedelta(days=rng.randint(1, 3))
        paid_on = received + timedelta(days=rng.randint(3, PAY_DAYS - 1))
        ym = received.strftime("%Y-%m")
        if ym in late and (ym not in forced or rng.random() < LATE_SHARE):
            delay = min(rng.randint(PAY_DAYS + 2, PAY_DAYS + 14), (latest_pay - received).days)
            if delay > PAY_DAYS:
                paid_on = received + timedelta(days=delay)
                forced.add(ym)
        amt = cents / 100
        tag = inv.lower()

        w.documents += [
            Document(id=f"doc-{tag}", kind=SourceKind.vendor_invoice, manager_id="keyway",
                     vendor_id=vendor.id, dated=received,
                     text=f"{vendor.name}. Invoice {inv[4:]}. {vendor.trade.title()} service. "
                          f"Amount due: {_money(cents)}. Terms: Net 10.",
                     data={"invoice_id": inv, "amount": amt, "received": received.isoformat()}),
            Document(id=f"doc-funding-{tag}", kind=SourceKind.funding, manager_id="keyway",
                     dated=funded_on,
                     text=f"Oakline Homes wire to Keyway Property Management, includes {inv} "
                          f"{_money(cents)}.",
                     data={"invoice_id": inv, "amount": amt, "funded_on": funded_on.isoformat()}),
            Document(id=f"doc-conf-{tag}", kind=SourceKind.vendor_confirmation, manager_id="keyway",
                     vendor_id=vendor.id, dated=min(paid_on + timedelta(days=2), w.as_of),
                     text=f"{vendor.name} confirms receipt of {_money(cents)} for {inv}.",
                     data={"invoice_id": inv, "status": "paid", "amount": amt,
                           "paid_on": paid_on.isoformat()}),
        ]
        _planted(w, inv, vendor.id, amt, received, amt, funded_on, amt, paid_on)
        by_month.setdefault(paid_on.strftime("%Y-%m"), []).append(
            {"invoice_id": inv, "vendor_id": vendor.id, "amount": amt, "on": paid_on.isoformat()})

    for ym, pays in sorted(by_month.items()):
        pays.sort(key=lambda p: (p["on"], p["invoice_id"]))
        year, month = map(int, ym.split("-"))
        eom = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
        w.documents += [
            Document(id=f"doc-stmt-keyway-{ym}-ops", kind=SourceKind.manager_statement,
                     manager_id="keyway", dated=min(eom, w.as_of),
                     text=f"Keyway Property Management monthly statement {ym}: " +
                          "; ".join(f"{p['on']} {p['invoice_id']} {_money(round(p['amount']*100))} PAID"
                                    for p in pays),
                     data={"lines": [{"line": f"L{i+1}", "invoice_id": p["invoice_id"],
                                      "amount": p["amount"], "status": "paid", "on": p["on"]}
                                     for i, p in enumerate(pays)]}),
            Document(id=f"doc-bank-keyway-{ym}-ops", kind=SourceKind.manager_bank,
                     manager_id="keyway", dated=min(eom + timedelta(days=5), w.as_of),
                     text=f"Keyway Property Management operating account {ym}: {len(pays)} "
                          "outgoing vendor payments.",
                     data={"payments": pays}),
        ]


def generate(seed: int = 13, scenarios: tuple[int, ...] = (3,), n_clean: int = 100,
             moment: bool | None = None) -> World:
    """scenarios: which planted scenarios to include (ALL_SCENARIOS for the full set).
    moment: plant the judge moment's electrician; defaults to on with the full scenario set."""
    rng = random.Random(seed)
    w = World(
        seed=seed, as_of=WORLD_TODAY, owner={"id": "oakline", "name": "Oakline Homes"},
        managers=list(MANAGERS), vendors=[Vendor(id=i, name=n, trade=t) for i, n, t in _VENDORS],
    )
    w.documents.append(Document(id="doc-agreement-keyway", kind=SourceKind.agreement,
                                manager_id="keyway", dated=date(2026, 1, 1), text=_AGREEMENT, data={}))
    w.terms = [{"manager_id": "keyway", "vendor_payment_days": PAY_DAYS,
                "holdback_pct": HOLDBACK_PCT, "approval_limit": APPROVAL_LIMIT}]
    w.recipe = {"scenarios": sorted(scenarios), "n_clean": n_clean}
    _clean_invoices(w, rng, n_clean, LATE_MONTHS["keyway"] if 4 in scenarios else set())
    for s in sorted(scenarios):
        SCENARIOS[s](w, rng)
    if moment if moment is not None else set(ALL_SCENARIOS) <= set(scenarios):
        plant_moment(w)
    w.recipe["moment"] = w.moment is not None
    return w
