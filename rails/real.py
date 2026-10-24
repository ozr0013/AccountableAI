"""Real implementation of the Rails protocol (contracts/steps.py) over Postgres."""

from __future__ import annotations

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


class RealRails:
    def __init__(self, schema: str | None = None):
        self.store = EventStore(conn=connect(schema))
        self.deps = DependencyIndex(conn=self.store.conn)

    # -- time -----------------------------------------------------------------------------
    def now(self) -> date:
        """The world's "today": WORLD_AS_OF, else the world_meta event, else the newest event.
        Never the wall clock, so demos and evals are reproducible."""
        if env := os.getenv("WORLD_AS_OF"):
            return date.fromisoformat(env)
        meta = self.store.events(type="world_meta")
        if meta:
            return date.fromisoformat(meta[-1].payload["as_of"])
        evs = self.store.events()
        return max((e.valid_from.date() for e in evs), default=date(2026, 1, 1))

    def _events(self, as_of: date | None = None) -> list[Event]:
        cutoff = datetime.combine(as_of, time.max, tzinfo=timezone.utc) if as_of else None
        return self.store.events(as_of=cutoff, current_only=True)

    # -- Rails protocol -------------------------------------------------------------------
    def append_event(self, event: Event) -> None:
        if self.store.append(event):
            self.deps.index_event(event)

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

    # -- loading a generated world --------------------------------------------------------
    def load(self, events: list[Event]) -> int:
        new = sum(self.store.append(e) for e in events)
        self.deps.rebuild(self.store.events())
        return new
