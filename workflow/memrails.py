"""In-memory Rails: same answers as rails.real.RealRails, no Postgres.

Used by tests and by `python -m workflow.run` when RAILS is not "real", so a whole world can be
replayed on a laptop. Everything is derived from the event list with Aaryan's pure rails
functions; derived views are cached per log version (the log is append-only).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

from contracts.models import Document, Event, LedgerLine, SourceKind
from rails import aging as _aging
from rails.docs import documents_from_events
from rails.facts import INVOICE_ATTRS, TERMS_ATTRS, fact_keys
from rails.guards import policy_check as _policy_check
from rails.ledger import build_ledger
from rails.matcher import find_duplicates, gather_evidence
from rails.recon import reconcile as _reconcile

_DUP_ATTRS = ("received", "amount", "vendor_ref", "service_period")


class MemoryRails:
    def __init__(self, events: list[Event] | tuple[Event, ...] = ()):
        self._events: list[Event] = []
        self._ids: set[str] = set()
        self._extra: set[tuple[str, str]] = set()
        self._cache: dict[tuple, Any] = {}
        for e in events:
            self.append_event(e)

    @classmethod
    def from_world(cls, scenarios: tuple[int, ...] = (3,), n_clean: int = 100, seed: int = 13):
        from world import generate
        return cls(generate(seed, scenarios, n_clean).events())

    # -- time -----------------------------------------------------------------------------
    def now(self) -> date:
        meta = [e for e in self._events if e.type == "world_meta"]
        if meta:
            return date.fromisoformat(meta[-1].payload["as_of"])
        return max((e.valid_from.date() for e in self._events), default=date(2026, 1, 1))

    def _current(self, as_of: date | None = None) -> list[Event]:
        key = ("current", len(self._events), as_of)
        if key not in self._cache:
            evs = self._events
            if as_of:
                cutoff = datetime.combine(as_of, time.max, tzinfo=UTC)
                evs = [e for e in evs if e.valid_from <= cutoff]
            replaced = {e.supersedes for e in evs if e.supersedes}
            self._cache[key] = [e for e in evs if e.id not in replaced]
        return self._cache[key]

    def _ledger(self, as_of: date | None = None) -> list[LedgerLine]:
        key = ("ledger", len(self._events), as_of)
        if key not in self._cache:
            self._cache[key] = build_ledger(self._current(as_of))
        return self._cache[key]

    # -- Rails protocol -------------------------------------------------------------------
    def append_event(self, event: Event) -> None:
        if event.id not in self._ids:      # replay of the same event is a no-op
            self._ids.add(event.id)
            self._events.append(event)

    def events(self, manager_id: str | None = None) -> list[Event]:
        return [e for e in self._events if manager_id in (None, e.manager_id)]

    def documents(self, manager_id: str | None = None, invoice_id: str | None = None) -> list[Document]:
        key = ("docs", len(self._events))
        if key not in self._cache:
            self._cache[key] = documents_from_events(self._current())
        docs = [d for d in self._cache[key] if manager_id in (None, d.manager_id)]
        if invoice_id:
            docs = [d for d in docs if d.data.get("invoice_id") in (None, invoice_id)]
        return docs

    def ledger(self, manager_id: str) -> list[LedgerLine]:
        return [l for l in self._ledger() if l.manager_id == manager_id]

    def unpaid(self, manager_id: str, as_of: date) -> list[LedgerLine]:
        return [l for l in self._ledger(as_of)
                if l.manager_id == manager_id and not l.duplicate_of
                and round(l.paid_amount * 100) < round(l.amount * 100)
                and (l.invoice_received is None or l.invoice_received <= as_of)]

    def late_months(self, manager_id: str) -> dict[str, bool]:
        return _aging.late_months(self._current(), manager_id, self.now())

    def reconcile(self, manager_id: str) -> dict[str, float]:
        return _reconcile(self._current(), manager_id)

    def record_dependency(self, item_id: str, fact_key: str) -> None:
        self._extra.add((item_id, fact_key))

    def dependents_of(self, fact_id: str) -> list[str]:
        """Same contract as rails.deps.DependencyIndex.dependents_of (rebuild semantics)."""
        event_facts, deps = self._index()
        keys = event_facts.get(fact_id) or {fact_id}
        return sorted({item for item, k in deps if k in keys})

    def policy_check(self, action: str, actor: str, payload: dict) -> tuple[bool, str]:
        return _policy_check(action, actor, payload)

    # -- dependency index (mirrors rails/deps/index.py rebuild) ---------------------------
    def _index(self) -> tuple[dict[str, set[str]], set[tuple[str, str]]]:
        key = ("index", len(self._events), len(self._extra))
        if key in self._cache:
            return self._cache[key]
        events = self._events
        facts: dict[str, set[str]] = {e.id: set(fact_keys(e)) for e in events}
        deps: set[tuple[str, str]] = set(self._extra)
        for e in events:
            if e.type == "document_ingested" and e.payload.get("kind") == SourceKind.vendor_invoice:
                if iid := e.payload.get("data", {}).get("invoice_id"):
                    deps |= {(iid, f"invoice:{iid}.{a}") for a in INVOICE_ATTRS}
                    if m := e.payload.get("manager_id"):
                        deps |= {(iid, f"terms:{m}.{a}") for a in TERMS_ATTRS}
        docs = documents_from_events(events)
        by_doc = {e.payload.get("id"): e.id for e in events if e.type == "document_ingested"}
        for iid, ev in gather_evidence(docs).evidence.items():
            for c in ev.cites.get("bank", []):
                if c.source_id in by_doc:
                    facts[by_doc[c.source_id]].add(f"invoice:{iid}.payment")
            if not ev.bank_cents:
                for _, c in ev.bank_docs:
                    if c.source_id in by_doc:
                        facts[by_doc[c.source_id]].add(f"invoice:{iid}.payment")
        for dup, orig in find_duplicates(docs).items():
            for a in _DUP_ATTRS:
                deps |= {(dup, f"invoice:{orig}.{a}"), (orig, f"invoice:{dup}.{a}")}
        self._cache[key] = (facts, deps)
        return facts, deps
