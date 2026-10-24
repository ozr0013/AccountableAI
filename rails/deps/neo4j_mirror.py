"""Neo4j mirror of the dependency table, for memory and the briefing view.

One-way and best-effort: Postgres stays the source of truth and `dependents_of()` never reads
this graph, so selective recheck works with Neo4j down. Sync is idempotent (MERGE everywhere).

Graph:  (Manager)<-[:OF]-(Invoice)-[:BILLED_BY]->(Vendor)
        (Invoice)-[:DEPENDS_ON]->(Fact)<-[:SETS]-(Source {event_id, doc_id, kind})
"""

from __future__ import annotations

import logging
import os

from contracts.models import Event

log = logging.getLogger(__name__)


class Neo4jMirror:
    def __init__(self, uri: str | None = None, user: str | None = None, password: str | None = None):
        from neo4j import GraphDatabase
        self.driver = GraphDatabase.driver(
            uri or os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            auth=(user or os.getenv("NEO4J_USER", "neo4j"),
                  password or os.getenv("NEO4J_PASSWORD", "accountable123")),
            connection_timeout=2, max_transaction_retry_time=1)

    def close(self) -> None:
        self.driver.close()

    def sync(self, events: list[Event], dependencies: list[tuple[str, str]],
             event_facts: list[tuple[str, str]]) -> None:
        inv = {}
        for e in events:
            if e.type == "document_ingested" and e.payload.get("kind") == "vendor_invoice":
                iid = e.payload["data"]["invoice_id"]
                inv[iid] = {"id": iid, "manager": e.payload.get("manager_id"),
                            "vendor": e.payload.get("vendor_id"), "amount": e.payload["data"]["amount"]}
        src = {e.id: {"event_id": e.id, "doc_id": e.payload.get("id"), "kind": e.payload.get("kind", e.type)}
               for e in events}
        with self.driver.session() as s:
            s.run("UNWIND $rows AS r MERGE (i:Invoice {id: r.id}) SET i.amount = r.amount "
                  "MERGE (m:Manager {id: r.manager}) MERGE (v:Vendor {id: r.vendor}) "
                  "MERGE (i)-[:OF]->(m) MERGE (i)-[:BILLED_BY]->(v)", rows=list(inv.values()))
            s.run("UNWIND $rows AS r MERGE (i:Invoice {id: r[0]}) MERGE (f:Fact {key: r[1]}) "
                  "MERGE (i)-[:DEPENDS_ON]->(f)", rows=[list(d) for d in dependencies if d[0] in inv])
            s.run("UNWIND $rows AS r MERGE (f:Fact {key: r[1]}) "
                  "MERGE (x:Source {event_id: r[0]}) SET x += $src[r[0]] MERGE (x)-[:SETS]->(f)",
                  rows=[list(r) for r in event_facts if r[0] in src], src=src)

    def dependents(self, fact_key: str) -> list[str]:
        """Same question as the Postgres table, answered from the graph (briefing/demo view)."""
        with self.driver.session() as s:
            return [r["id"] for r in s.run(
                "MATCH (i:Invoice)-[:DEPENDS_ON]->(:Fact {key: $k}) RETURN i.id AS id ORDER BY id",
                k=fact_key)]

    def sources_for(self, invoice_id: str) -> list[dict]:
        """Records that set any fact this invoice depends on (source links for the briefing)."""
        with self.driver.session() as s:
            return [dict(r) for r in s.run(
                "MATCH (:Invoice {id: $i})-[:DEPENDS_ON]->(f:Fact)<-[:SETS]-(x:Source) "
                "RETURN DISTINCT x.doc_id AS doc_id, x.kind AS kind ORDER BY doc_id", i=invoice_id)]
