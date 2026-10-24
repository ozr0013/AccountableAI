import os

import pytest

from rails import RealRails
from rails.deps.neo4j_mirror import Neo4jMirror
from world import generate


def neo4j_up() -> bool:
    try:
        m = Neo4jMirror()
        m.driver.verify_connectivity()
        m.close()
        return True
    except Exception:
        return False


def test_recheck_works_with_neo4j_down(schema, monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:1")           # nothing listens here
    monkeypatch.setenv("MEMORY", "neo4j")
    r = RealRails(schema=schema)
    r.load(generate(scenarios=(3, 5), n_clean=5).events())           # mirror fails quietly
    assert r.sync_memory() is False
    assert r.dependents_of("invoice:INV-KW-0402.received") == ["INV-KW-0402"]


def test_memory_postgres_skips_mirror(schema, monkeypatch):
    monkeypatch.setenv("MEMORY", "postgres")
    assert RealRails(schema=schema).sync_memory() is False


@pytest.mark.skipif(not neo4j_up(), reason="neo4j not running (docker compose up -d neo4j)")
def test_mirror_agrees_with_postgres_and_links_sources(schema):
    r = RealRails(schema=schema)
    r.load(generate(scenarios=(3, 5), n_clean=5).events())
    assert r.sync_memory() is True
    m = Neo4jMirror()
    try:
        m.driver.session().run("RETURN 1").consume()
        mine = {l.invoice_id for m_ in ("keyway", "sunbelt", "peachtree") for l in r.ledger(m_)}
        for key in ("invoice:INV-KW-0402.received", "invoice:INV-KW-0301.payment",
                    "terms:keyway.vendor_payment_days"):
            assert [i for i in m.dependents(key) if i in mine] == r.dependents_of(key), key
        docs = {s["doc_id"] for s in m.sources_for("INV-KW-0301")}
        assert {"doc-inv-kw-0301", "doc-bank-kw-2026-03", "doc-stmt-kw-2026-03"} <= docs
        r.sync_memory()          # idempotent
        assert m.dependents("invoice:INV-KW-0402.received") == ["INV-KW-0402"]
    finally:
        m.close()
