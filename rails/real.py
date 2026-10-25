"""Real implementation of the Rails protocol (contracts/steps.py) over Postgres."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, time, timezone

from contracts.models import Document, Event, LedgerLine

from . import aging as _aging
from .db import connect
from .deps import DependencyIndex
from .docs import documents_from_events
from .events import EventStore
from .ledger import build_ledger
from .recon import reconcile as _reconcile


log = logging.getLogger(__name__)


class WorldMismatch(RuntimeError):
    """The database holds a world generated with different settings."""


def _recipe(meta: dict) -> str:
    keys = ("seed", "scenarios", "n_clean", "moment", "as_of")
    return ", ".join(f"{k}={meta[k]}" for k in keys if k in meta)


def books_backend() -> str:
    """Where the books live: 'spacetime' (BOOKS=spacetime, the system of record in SpacetimeDB)
    or 'postgres' (the original event store)."""
    return os.getenv("BOOKS", "postgres").lower()


class RealRails:
    def __init__(self, schema: str | None = None):
        if books_backend() == "spacetime":
            from .events.spacetime_store import SpacetimeDependencyIndex, SpacetimeEventStore
            self.store = SpacetimeEventStore(book=schema or "public")
            self.deps = SpacetimeDependencyIndex(self.store)
        else:
            self.store = EventStore(conn=connect(schema))
            self.deps = DependencyIndex(conn=self.store.conn)

    # -- time -----------------------------------------------------------------------------
    def now(self) -> date:
        """The world's "today": WORLD_AS_OF, else the latest `clock_advanced` event or the
        world_meta date (whichever is later), else the newest event. Never the wall clock, so
        demos and evals are reproducible. Time only moves forward."""
        if env := os.getenv("WORLD_AS_OF"):
            return date.fromisoformat(env)
        meta = self.store.events(type="world_meta")
        if meta:
            days = [date.fromisoformat(e.payload["as_of"])
                    for e in meta + self.store.events(type="clock_advanced")]
            return max(days)
        evs = self.store.events()
        return max((e.valid_from.date() for e in evs), default=date(2026, 1, 1))

    def _events(self, as_of: date | None = None) -> list[Event]:
        cutoff = datetime.combine(as_of, time.max, tzinfo=timezone.utc) if as_of else None
        return self.store.events(as_of=cutoff, current_only=True)

    # -- Rails protocol -------------------------------------------------------------------
    def append_event(self, event: Event) -> None:
        if self.store.append(event):
            self.deps.index_event(event)
            from rails.live.spacetime import mirror_new
            mirror_new(self.store, event)

    def events(self, manager_id: str | None = None) -> list[Event]:
        return self.store.events(manager_id=manager_id)

    def documents(self, manager_id: str | None = None, invoice_id: str | None = None) -> list[Document]:
        docs = [d for d in documents_from_events(self._events())
                if manager_id in (None, d.manager_id)]
        if invoice_id:
            docs = [d for d in docs if d.data.get("invoice_id") in (None, invoice_id)]
        return docs

    def ledger(self, manager_id: str) -> list[LedgerLine]:
        return [l for l in build_ledger(self._events()) if l.manager_id == manager_id]

    def unpaid(self, manager_id: str, as_of: date) -> list[LedgerLine]:
        """Invoices received by `as_of` that, on what existed by then, are not fully paid."""
        return [l for l in build_ledger(self._events(as_of))
                if l.manager_id == manager_id and not l.duplicate_of
                and round(l.paid_amount * 100) < round(l.amount * 100)
                and (l.invoice_received is None or l.invoice_received <= as_of)]

    def late_months(self, manager_id: str) -> dict[str, bool]:
        return _aging.late_months(self._events(), manager_id, self.now())

    def reconcile(self, manager_id: str) -> dict[str, float]:
        return _reconcile(self._events(), manager_id)

    def dependents_of(self, fact_id: str) -> list[str]:
        """fact_id is an event id (the facts it touched) or a fact key like
        `invoice:INV-1.received`. Returns the invoice / alert ids to recheck."""
        return self.deps.dependents_of(fact_id)

    def record_dependency(self, item_id: str, fact_key: str) -> None:
        """Optional extra: tie a non-invoice item (alert, pattern) to a fact."""
        self.deps.record_dependency(item_id, fact_key)

    def policy_check(self, action: str, actor: str, payload: dict) -> tuple[bool, str]:
        from .guards import policy_check
        return policy_check(action, actor, payload)

    def sync_memory(self) -> bool:
        """Mirror dependencies to Neo4j for memory/briefing. Best effort: returns False (and
        carries on) when MEMORY isn't neo4j or Neo4j is down. Recheck never depends on this."""
        if os.getenv("MEMORY", "neo4j").lower() != "neo4j":
            return False
        try:
            from .deps.neo4j_mirror import Neo4jMirror
            m = Neo4jMirror()
            try:
                if self.store.conn is None:
                    deps, facts = self.deps.dependency_rows(), self.deps.fact_rows()
                else:
                    c = self.store.conn
                    deps = c.execute("SELECT item_id, fact_key FROM dependencies").fetchall()
                    facts = c.execute("SELECT event_id, fact_key FROM event_facts").fetchall()
                m.sync(self.store.events(current_only=True), deps, facts)
            finally:
                m.close()
            return True
        except Exception as e:      # noqa: BLE001 - mirror must never break the rails
            log.warning("neo4j mirror skipped: %s", e)
            return False

    # -- loading a generated world --------------------------------------------------------
    def load(self, events: list[Event]) -> int:
        """Append a generated world. The log is append-only, so loading a *different* world on
        top of an existing one is refused with a clear message instead of a half-load."""
        incoming = next((e for e in events if e.type == "world_meta"), None)
        existing = self.store.get("ev-world-meta")
        if incoming and existing and existing.payload != incoming.payload:
            raise WorldMismatch(
                f"The books already hold a different world ({_recipe(existing.payload)}); this "
                f"one is ({_recipe(incoming.payload)}). Reset it with `python -m rails.load "
                f"--fresh` plus the same --scenarios/--clean, or run without RAILS=real.")
        new = sum(self.store.append(e) for e in events)
        self.deps.rebuild(self.store.events())
        self.sync_memory()
        return new
