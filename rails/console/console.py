"""Judge Console backend: presets and free text become events.

A judge injects one real fact ("the electrician's invoice arrived Apr 1"); this turns it into
an append-only event and reports which items depend on it (`dependents_of`) so the workflow
rechecks only those. Free text goes through a constrained deterministic parser. If the text
doesn't map cleanly onto a known event, it is rejected with suggestions. Never guessed.

Events are deterministic in their ids, so clicking the same preset twice is a no-op.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone

from contracts.models import Citation, Document, Event, SourceKind

from ..docs import documents_from_events
from ..events.ingest import document_event


class ConsoleError(ValueError):
    def __init__(self, message: str, suggestions: list[str] | None = None):
        super().__init__(message)
        self.suggestions = suggestions or []


@dataclass
class Preset:
    id: str
    label: str
    description: str
    params: dict[str, str]                    # name -> help
    defaults: dict[str, str] = field(default_factory=dict)


PRESETS = [
    Preset("invoice_arrived", "Invoice actually arrived on a date",
           "Corrects the receipt date of one invoice; the payment deadline moves with it.",
           {"invoice_id": "invoice id", "date": "YYYY-MM-DD"},
           {"invoice_id": "INV-KW-0402", "date": "2026-04-01"}),
    Preset("manager_replies_paid", "Manager replies \"paid\"",
           "Reply to a chase claiming payment, with no proof attached.",
           {"invoice_id": "invoice id"}, {"invoice_id": "INV-KW-0301"}),
    Preset("vendor_confirms_paid", "Vendor confirms it was paid",
           "The vendor says it received the money.",
           {"invoice_id": "invoice id", "paid_on": "YYYY-MM-DD"},
           {"invoice_id": "INV-KW-0301", "paid_on": "2026-04-24"}),
    Preset("bank_shows_payment", "Bank record shows the payment",
           "The manager's bank records show an outgoing payment to the vendor.",
           {"invoice_id": "invoice id", "on": "YYYY-MM-DD"},
           {"invoice_id": "INV-KW-0301", "on": "2026-04-24"}),
    Preset("terms_changed", "Agreement payment window corrected",
           "Corrects the vendor payment window (days) in the confirmed terms.",
           {"manager_id": "manager id", "days": "number of days"},
           {"manager_id": "keyway", "days": "15"}),
    Preset("vendor_requests_bank_change", "Vendor asks to change bank details",
           "Held for identity verification and controller approval.",
           {"vendor_id": "vendor id", "invoice_id": "invoice id (optional)"},
           {"vendor_id": "v-voltwise", "invoice_id": "INV-SB-0701"}),
]
_BY_ID = {p.id: p for p in PRESETS}


@dataclass
class ConsoleResult:
    summary: str
    events: list[Event]
    affected: list[str]              # items to recheck, from dependents_of

    @property
    def event_ids(self) -> list[str]:
        return [e.id for e in self.events]


class JudgeConsole:
    def __init__(self, rails):
        self.rails = rails

    # -- public ---------------------------------------------------------------------------
    def presets(self) -> list[Preset]:
        return PRESETS

    def apply_preset(self, preset_id: str, params: dict | None = None) -> ConsoleResult:
        if preset_id not in _BY_ID:
            raise ConsoleError(f"unknown preset {preset_id!r}", [p.id for p in PRESETS])
        p = {**_BY_ID[preset_id].defaults, **(params or {})}
        missing = [k for k in _BY_ID[preset_id].params if k not in p and k != "invoice_id (optional)"
                   and not (preset_id == "vendor_requests_bank_change" and k == "invoice_id")]
        if missing:
            raise ConsoleError(f"missing {', '.join(missing)}")
        events, summary = getattr(self, f"_{preset_id}")(p)
        return self._commit(events, summary)

    def apply_text(self, text: str, manager_id: str | None = None) -> ConsoleResult:
        from .parse import parse
        preset_id, params = parse(text, self, manager_id)
        return self.apply_preset(preset_id, params)

    # -- lookups (also used by the parser) ------------------------------------------------
    def now(self) -> datetime:
        return datetime.combine(self.rails.now(), time(12), tzinfo=timezone.utc)

    def invoices(self) -> list[Document]:
        return [d for d in documents_from_events(self.rails.store.events(current_only=True))
                if d.kind == SourceKind.vendor_invoice]

    def vendors(self) -> dict[str, dict]:
        return {e.payload["id"]: e.payload for e in self.rails.store.events(type="entity_registered")
                if e.payload.get("kind") == "vendor"}

    def managers(self) -> dict[str, dict]:
        return {e.payload["id"]: e.payload for e in self.rails.store.events(type="entity_registered")
                if e.payload.get("kind") == "manager"}

    def invoice(self, invoice_id: str) -> Document:
        for d in self.invoices():
            if d.data["invoice_id"] == invoice_id:
                return d
        raise ConsoleError(f"no invoice {invoice_id!r}",
                           sorted(d.data["invoice_id"] for d in self.invoices())[:10])

    # -- helpers --------------------------------------------------------------------------
    @staticmethod
    def _date(s: str, field_name: str) -> date:
        try:
            return date.fromisoformat(str(s))
        except ValueError:
            raise ConsoleError(f"{field_name} must be YYYY-MM-DD, got {s!r}") from None

    def _eid(self, kind: str, params: dict) -> str:
        h = hashlib.sha1(json.dumps([kind, params], sort_keys=True).encode()).hexdigest()[:10]
        return f"ev-judge-{kind}-{h}"

    def _source(self, eid: str) -> Citation:
        return Citation(source_id=eid, kind=SourceKind.judge)

    def _commit(self, events: list[Event], summary: str) -> ConsoleResult:
        affected: list[str] = []
        for e in events:
            self.rails.append_event(e)
            for x in self.rails.dependents_of(e.id):
                if x not in affected:
                    affected.append(x)
        return ConsoleResult(summary, events, affected)

    def _correction(self, eid: str, fact_key: str, value, manager_id, invoice_id,
                    valid_from: datetime) -> Event:
        """New fact_corrected event; supersedes an earlier correction of the same fact."""
        same = [e for e in self.rails.store.events(type="fact_corrected", current_only=True)
                if e.payload["fact_key"] == fact_key and e.id != eid]
        return Event(id=eid, type="fact_corrected", manager_id=manager_id, invoice_id=invoice_id,
                     valid_from=valid_from, learned_at=self.now(),
                     supersedes=same[-1].id if same else None,
                     payload={"fact_key": fact_key, "value": value}, source=self._source(eid))

    # -- presets --------------------------------------------------------------------------
    def _invoice_arrived(self, p):
        inv = self.invoice(p["invoice_id"])
        day = self._date(p["date"], "date")
        eid = self._eid("invoice_arrived", {"i": p["invoice_id"], "d": day.isoformat()})
        ev = self.rails.store.get(eid) or self._correction(
            eid, f"invoice:{p['invoice_id']}.received", day.isoformat(), inv.manager_id,
            p["invoice_id"], datetime.combine(day, time.min, tzinfo=timezone.utc))
        return [ev], f"{p['invoice_id']} arrived {day:%b} {day.day}; its payment deadline moves with it."

    def _terms_changed(self, p):
        if p["manager_id"] not in self.managers():
            raise ConsoleError(f"no manager {p['manager_id']!r}", sorted(self.managers()))
        try:
            days = int(p["days"])
        except ValueError:
            raise ConsoleError(f"days must be a whole number, got {p['days']!r}") from None
        eid = self._eid("terms_changed", {"m": p["manager_id"], "d": days})
        ev = self.rails.store.get(eid) or self._correction(
            eid, f"terms:{p['manager_id']}.vendor_payment_days", days, p["manager_id"], None,
            self.now())
        return [ev], f"{p['manager_id']} payment window is now {days} days."

    def _manager_replies_paid(self, p):
        inv = self.invoice(p["invoice_id"])
        eid = self._eid("manager_replies_paid", {"i": p["invoice_id"]})
        text = "We paid this vendor already."
        ev = Event(id=eid, type="chase_reply", manager_id=inv.manager_id, invoice_id=p["invoice_id"],
                   valid_from=self.now(), learned_at=self.now(),
                   payload={"id": eid, "manager_id": inv.manager_id, "invoice_ids": [p["invoice_id"]],
                            "text": text, "received_at": self.now().isoformat(),
                            "claims_paid": True, "proof": []},
                   source=Citation(source_id=eid, kind=SourceKind.manager_reply))
        return [ev], f"Manager replied paid on {p['invoice_id']}; no vendor or bank proof attached."

    def _vendor_confirms_paid(self, p):
        inv = self.invoice(p["invoice_id"])
        day = self._date(p.get("paid_on") or self.rails.now().isoformat(), "paid_on")
        doc = Document(
            id=self._eid("vendor_confirms_paid", {"i": p["invoice_id"], "d": day.isoformat()}),
            kind=SourceKind.vendor_confirmation, manager_id=inv.manager_id, vendor_id=inv.vendor_id,
            dated=day, text=f"Vendor confirms receipt of payment for {p['invoice_id']} on {day}.",
            data={"invoice_id": p["invoice_id"], "status": "paid", "amount": inv.data["amount"],
                  "paid_on": day.isoformat()})
        return [document_event(doc, self.now())], f"Vendor confirmed {p['invoice_id']} was paid."

    def _bank_shows_payment(self, p):
        inv = self.invoice(p["invoice_id"])
        day = self._date(p.get("on") or self.rails.now().isoformat(), "on")
        doc = Document(
            id=self._eid("bank_shows_payment", {"i": p["invoice_id"], "d": day.isoformat()}),
            kind=SourceKind.manager_bank, manager_id=inv.manager_id, dated=day,
            text=f"Bank record shows payment of {p['invoice_id']} on {day}.",
            data={"payments": [{"invoice_id": p["invoice_id"], "vendor_id": inv.vendor_id,
                                "amount": inv.data["amount"], "on": day.isoformat()}]})
        return [document_event(doc, self.now())], f"Bank record shows {p['invoice_id']} paid {day}."

    def _vendor_requests_bank_change(self, p):
        if p["vendor_id"] not in self.vendors():
            raise ConsoleError(f"no vendor {p['vendor_id']!r}", sorted(self.vendors())[:10])
        eid = self._eid("bank_change", {"v": p["vendor_id"], "i": p.get("invoice_id")})
        ev = Event(id=eid, type="bank_change_requested", invoice_id=p.get("invoice_id"),
                   valid_from=self.now(), learned_at=self.now(),
                   payload={"vendor_id": p["vendor_id"],
                            "decision": self.rails.policy_check("request_bank_change", "judge", {})[1]},
                   source=self._source(eid))
        return [ev], f"{p['vendor_id']} asked to change bank details: held for identity verification."
