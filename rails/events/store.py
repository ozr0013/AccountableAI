"""Append-only event log with two timelines.

valid_from = when it was true in the world; learned_at = when we found out.
Corrections never edit history: a new event points at the one it `supersedes`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from contracts.models import Event

from ..db import connect

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

_COLS = "id, type, manager_id, invoice_id, valid_from, learned_at, supersedes, payload, source"


class ConflictingEvent(Exception):
    """Same event id re-appended with different content."""


def content_hash(event: Event) -> str:
    body = event.model_dump(mode="json")
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _row_to_event(row) -> Event:
    return Event(
        id=row[0], type=row[1], manager_id=row[2], invoice_id=row[3],
        valid_from=row[4], learned_at=row[5], supersedes=row[6],
        payload=row[7], source=row[8],
    )


class EventStore:
    def __init__(self, conn: psycopg.Connection | None = None, schema: str | None = None):
        self.conn = conn or connect(schema)
        self.conn.execute(SCHEMA)

    def append(self, event: Event) -> bool:
        """Append an event. Returns False if the identical event is already there (replay)."""
        digest = content_hash(event)
        row = self.conn.execute(
            f"""INSERT INTO events ({_COLS}, content_hash)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING RETURNING seq""",
            (event.id, event.type, event.manager_id, event.invoice_id,
             event.valid_from, event.learned_at, event.supersedes,
             Jsonb(event.payload),
             Jsonb(event.source.model_dump(mode="json")) if event.source else None,
             digest),
        ).fetchone()
        if row:
            return True
        existing = self.conn.execute(
            "SELECT content_hash FROM events WHERE id = %s", (event.id,)
        ).fetchone()
        if existing[0] != digest:
            raise ConflictingEvent(f"event {event.id} already exists with different content")
        return False

    def get(self, event_id: str) -> Event | None:
        row = self.conn.execute(
            f"SELECT {_COLS} FROM events WHERE id = %s", (event_id,)
        ).fetchone()
        return _row_to_event(row) if row else None

    def events(
        self,
        manager_id: str | None = None,
        invoice_id: str | None = None,
        type: str | None = None,
        as_of: datetime | None = None,
        known_at: datetime | None = None,
        current_only: bool = False,
    ) -> list[Event]:
        """Events in append order.

        as_of: only facts true by then (valid_from <= as_of).
        known_at: only what we knew by then (learned_at <= known_at).
        current_only: drop events that a later (visible) event supersedes.
        """
        where, args = ["TRUE"], []
        for col, val in (("manager_id", manager_id), ("invoice_id", invoice_id), ("type", type)):
            if val is not None:
                where.append(f"{col} = %s")
                args.append(val)
        if as_of is not None:
            where.append("valid_from <= %s")
            args.append(as_of)
        if known_at is not None:
            where.append("learned_at <= %s")
            args.append(known_at)
        rows = self.conn.execute(
            f"SELECT {_COLS} FROM events WHERE {' AND '.join(where)} ORDER BY seq", args
        ).fetchall()
        out = [_row_to_event(r) for r in rows]
        if current_only:
            replaced = {e.supersedes for e in out if e.supersedes}
            out = [e for e in out if e.id not in replaced]
        return out

    def chain(self, event_id: str) -> list[Event]:
        """The correction history ending at event_id, oldest first."""
        out, cur = [], self.get(event_id)
        while cur:
            out.append(cur)
            cur = self.get(cur.supersedes) if cur.supersedes else None
        return list(reversed(out))
