"""Postgres connection helper shared by the rails modules."""

from __future__ import annotations

import os

import psycopg

DEFAULT_URL = "postgresql://accountable:accountable@localhost:5432/accountable"


def database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_URL)


def connect(schema: str | None = None) -> psycopg.Connection:
    """Autocommit connection; optionally pinned to a schema (used by tests)."""
    conn = psycopg.connect(database_url(), autocommit=True)
    if schema:
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        conn.execute(f'SET search_path TO "{schema}"')
    return conn
