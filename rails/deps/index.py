"""dependents_of(): which items must be rechecked when a fact changes.

Lives in Postgres so selective recheck never depends on Neo4j being up (Neo4j is a mirror for
memory and the briefing view, see rails/deps/neo4j_mirror.py).

Rails fills the table itself: every result rails computes is a function of facts it already
parses, so an invoice depends on its own `invoice:<id>.*` facts and on its manager's terms.
`record_dependency` adds explicit extras (e.g. an alert or pattern item) for the workflow.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

from contracts.models import Event, SourceKind

from ..db import connect
from ..docs import documents_from_events
from ..facts import INVOICE_ATTRS, TERMS_ATTRS, fact_keys
from ..matcher import find_duplicates, gather_evidence

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()
_DUP_ATTRS = ("received", "amount", "vendor_ref", "service_period")


class DependencyIndex:
    def __init__(self, conn: psycopg.Connection | None = None, schema: str | None = None):
        self.conn = conn or connect(schema)
        self.conn.execute(SCHEMA)

    # -- writes ---------------------------------------------------------------------------
    def _facts(self, event_id: str, keys: set[str]) -> None:
        for k in keys:
            self.conn.execute(
                "INSERT INTO event_facts VALUES (%s,%s) ON CONFLICT DO NOTHING", (event_id, k))

    def record_dependency(self, item_id: str, fact_key: str) -> None:
        self.conn.execute(
            "INSERT INTO dependencies VALUES (%s,%s) ON CONFLICT DO NOTHING", (item_id, fact_key))

    def _invoice_deps(self, iid: str, manager_id: str | None) -> None:
        for a in INVOICE_ATTRS:
            self.record_dependency(iid, f"invoice:{iid}.{a}")
        for a in TERMS_ATTRS:
            if manager_id:
                self.record_dependency(iid, f"terms:{manager_id}.{a}")

    def index_event(self, e: Event) -> None:
        """Incremental path used on append."""
        self._facts(e.id, fact_keys(e))
        if e.type == "document_ingested" and e.payload.get("kind") == SourceKind.vendor_invoice:
            if iid := e.payload.get("data", {}).get("invoice_id"):
                self._invoice_deps(iid, e.payload.get("manager_id"))

    def rebuild(self, events: list[Event]) -> None:
        """Recompute everything from the log. Also ties payments the matcher matched without an
        invoice id, bank records that show no payment to the still-open items they cover, and
        links duplicate invoices to their originals."""
        self.conn.execute("TRUNCATE event_facts, dependencies")
        for e in events:
            self.index_event(e)
        docs = documents_from_events(events)
        by_doc = {e.payload.get("id"): e.id for e in events if e.type == "document_ingested"}
        for iid, ev in gather_evidence(docs).evidence.items():
            for c in ev.cites.get("bank", []):
                if c.source_id in by_doc:
                    self._facts(by_doc[c.source_id], {f"invoice:{iid}.payment"})
            if not ev.bank_cents:       # "no payment found" is a fact about the still-open items
                for _, c in ev.bank_docs:
                    if c.source_id in by_doc:
                        self._facts(by_doc[c.source_id], {f"invoice:{iid}.payment"})
        for dup, orig in find_duplicates(docs).items():
            for a in _DUP_ATTRS:
                self.record_dependency(dup, f"invoice:{orig}.{a}")
                self.record_dependency(orig, f"invoice:{dup}.{a}")

    # -- reads ----------------------------------------------------------------------------
    def keys_for(self, x: str) -> set[str]:
        """x is an event id (resolved to the facts it touched) or a raw fact key."""
        rows = self.conn.execute(
            "SELECT fact_key FROM event_facts WHERE event_id = %s", (x,)).fetchall()
        return {r[0] for r in rows} or {x}

    def dependents_of(self, x: str) -> list[str]:
        keys = list(self.keys_for(x))
        rows = self.conn.execute(
            "SELECT DISTINCT item_id FROM dependencies WHERE fact_key = ANY(%s) ORDER BY item_id",
            (keys,)).fetchall()
        return [r[0] for r in rows]
