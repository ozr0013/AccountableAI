"""Deterministic world generator.

Same seed -> same world. Emits documents, ledger lines and planted ground truth;
it never touches the database. Scenario docs are built verbatim from the shared
fixture shapes so contracts/fixtures/sample/ stays the single source of truth.

Early version: scenario 3 + clean invoices. Add scenarios in `SCENARIOS`.
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


SCENARIOS: dict[int, Callable[[World, random.Random], None]] = {3: scenario_3, 5: scenario_5}


# --- clean background: funded, paid on time, confirmed by vendor ------------------------------

def _clean_invoices(w: World, rng: random.Random, n: int) -> None:
    """n clean invoices spread across managers, Jan 5 to Apr 10. Paid within the 10-day term."""
    vendors_by_mgr = {
        m.id: [v for i, v in enumerate(w.vendors) if i % len(w.managers) == j]
        for j, m in enumerate(w.managers)
    }
    vendors_by_mgr["keyway"] = [w.vendors[0]] + vendors_by_mgr["keyway"]  # scenario 3 plumber
    per_mgr_month: dict[tuple[str, str], list[dict]] = {}
    counters = {m.id: 0 for m in w.managers}
    start, span = date(2026, 1, 5), (date(2026, 4, 10) - date(2026, 1, 5)).days

    for k in range(n):
        m = w.managers[k % len(w.managers)]
        vendor = rng.choice(vendors_by_mgr[m.id])
        counters[m.id] += 1
        inv = f"INV-{_CODE[m.id]}-{1000 + counters[m.id]}"
        cents = rng.randrange(8_000, 450_000, 5)           # $80 to $4,500, always under $5,000
        received = start + timedelta(days=rng.randrange(span))
        funded_on = received + timedelta(days=rng.randint(1, 3))
        paid_on = received + timedelta(days=rng.randint(3, PAY_DAYS - 1))
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
    rng = random.Random(seed)
    w = World(
        seed=seed, as_of=AS_OF, owner={"id": "oakline", "name": "Oakline Homes"},
        managers=list(MANAGERS), vendors=[Vendor(id=i, name=n, trade=t) for i, n, t in _VENDORS],
    )
    w.documents += [
        Document(id=f"doc-agreement-{m.id}", kind=SourceKind.agreement, manager_id=m.id,
                 dated=date(2026, 1, 1), text=_AGREEMENT, data={})
        for m in w.managers
    ]
    w.terms = [{"manager_id": m.id, "vendor_payment_days": PAY_DAYS} for m in w.managers]
    for s in sorted(scenarios):
        SCENARIOS[s](w, rng)
    _clean_invoices(w, rng, n_clean)
    return w
