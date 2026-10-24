import uuid

import psycopg
import pytest

from rails.db import connect


@pytest.fixture
def schema():
    """Throwaway Postgres schema per test; skips if the database isn't up."""
    name = f"t_{uuid.uuid4().hex[:10]}"
    try:
        conn = connect(name)
    except psycopg.OperationalError:
        pytest.skip("postgres not running (docker compose up -d postgres)")
    yield name
    conn.execute(f'DROP SCHEMA "{name}" CASCADE')
    conn.close()


@pytest.fixture(autouse=True)
def _no_neo4j_mirror(monkeypatch):
    """Tests never write into the dev Neo4j graph; the live mirror test opts back in."""
    monkeypatch.setenv("MEMORY", "postgres")
