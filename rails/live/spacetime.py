"""Mirror the PostgreSQL tables into SpacetimeDB so the dashboard can subscribe live.

PostgreSQL stays the source of truth. Each event is published through the module's
`publish_event` reducer over HTTP; the reducer ignores ids it already has, so a backfill or
a retry never duplicates rows.

    python -m rails.live.spacetime --backfill        # copy all five tables
    python -m rails.live.spacetime --check           # compare row counts per table

Env: SPACETIME_URL (default http://127.0.0.1:3100), SPACETIME_DB (default accountableai),
SPACETIME_TOKEN (the publisher's token: `spacetime login show --token`).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

URL = os.getenv("SPACETIME_URL", "http://127.0.0.1:3100")
DB = os.getenv("SPACETIME_DB", "accountableai")


def _token() -> str:
    token = os.getenv("SPACETIME_TOKEN")
    if token:
        return token
    out = subprocess.run(["spacetime", "login", "show", "--token"], capture_output=True,
                         text=True, check=True).stdout
    found = [w for w in out.split() if w.startswith("ey")]
    if not found:
        raise RuntimeError("set SPACETIME_TOKEN or log in with the spacetime CLI")
    return found[0]


def _option(value: str | None) -> dict:
    return {"none": []} if value is None else {"some": value}


def _post(path: str, body, token: str, content_type="application/json") -> bytes:
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(f"{URL}/v1/database/{DB}/{path}", data=data, method="POST",
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": content_type})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read()


class SpacetimeMirror:
    def __init__(self, token: str | None = None):
        self.token = token or _token()

    def publish(self, seq: int, event) -> None:
        """Publish one contracts.models.Event. Raises on HTTP errors so callers can retry."""
        args = [event.id, seq, event.type, _option(event.manager_id), _option(event.invoice_id),
                event.valid_from.isoformat(), event.learned_at.isoformat(),
                json.dumps(event.payload, sort_keys=True, default=str)]
        _post("call/publish_event", args, self.token)

    def count(self, table: str = "event") -> int:
        rows = json.loads(_post("sql", f"SELECT * FROM {table}".encode(), self.token, "text/plain"))
        return len(rows[0]["rows"]) if rows else 0

    def call(self, reducer: str, args: list) -> None:
        _post("call/" + reducer, args, self.token)


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


# Postgres table -> (SpacetimeDB table, SELECT, reducer args from a row).
TABLES = {
    "agent_cases": ("agent_case", "upsert_case",
                    "SELECT id, owner_id, manager_id, vendor_id, invoice_id, body->>'state', "
                    "version, body FROM agent_cases",
                    lambda r: [r[0], r[1], r[2], r[3], r[4], r[5] or "", r[6],
                               json.dumps(r[7], sort_keys=True, default=str)]),
    "scheduled_jobs": ("scheduled_job", "upsert_job",
                       "SELECT id, case_id, schedule_key, due_at, status, attempts, lease_owner, "
                       "lease_until, last_error, payload FROM scheduled_jobs",
                       lambda r: [r[0], r[1], r[2], _iso(r[3]), r[4], r[5], _option(r[6]),
                                  _option(_iso(r[7])), _option(r[8]),
                                  json.dumps(r[9], sort_keys=True, default=str)]),
    "event_facts": ("event_fact", "put_fact", "SELECT event_id, fact_key FROM event_facts",
                    lambda r: [r[0], r[1]]),
    "dependencies": ("dependency", "put_dependency", "SELECT item_id, fact_key FROM dependencies",
                     lambda r: [r[0], r[1]]),
}


def _exists(conn, table: str) -> bool:
    return conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is not None


def sync_tables(conn, mirror: SpacetimeMirror) -> dict[str, tuple[int, int]]:
    """Copy every non-event table. Returns {table: (rows sent, failures)}."""
    mirror.call("clear_derived", [])
    out = {}
    for pg_table, (_, reducer, sql, to_args) in TABLES.items():
        if not _exists(conn, pg_table):
            continue
        rows, failed = conn.execute(sql).fetchall(), 0
        for row in rows:
            try:
                mirror.call(reducer, to_args(row))
            except urllib.error.URLError as exc:
                failed += 1
                log.warning("%s row failed: %s", pg_table, exc)
        out[pg_table] = (len(rows), failed)
    return out


_MIRROR: SpacetimeMirror | None = None


def mirror_new(store, event) -> None:
    """Best effort, after a Postgres commit: the workflow never waits on or fails for the mirror.
    On only when SPACETIME=on; a missed event is repaired by the next --backfill."""
    global _MIRROR
    if os.getenv("SPACETIME", "off").lower() != "on":
        return
    try:
        _MIRROR = _MIRROR or SpacetimeMirror()
        row = store.conn.execute("SELECT seq FROM events WHERE id=%s", (event.id,)).fetchone()
        _MIRROR.publish(row[0], event)
    except Exception as exc:  # noqa: BLE001 - the mirror must never break the source of truth
        log.warning("spacetime mirror skipped %s: %s", event.id, exc)


def postgres_events(store):
    """(seq, Event) in log order from the real rails store."""
    rows = store.conn.execute("SELECT seq, id FROM events ORDER BY seq").fetchall()
    order = {eid: seq for seq, eid in rows}
    return sorted(((order[e.id], e) for e in store.events() if e.id in order), key=lambda p: p[0])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    from pathlib import Path

    from dotenv import load_dotenv
    load_dotenv(Path.cwd() / ".env", override=False)
    from rails.real import RealRails
    rails = RealRails()
    mirror = SpacetimeMirror()
    events = postgres_events(rails.store)
    if args.backfill:
        failed = 0
        for seq, event in events:
            try:
                mirror.publish(seq, event)
            except urllib.error.URLError as exc:
                failed += 1
                log.warning("publish %s failed: %s", event.id, exc)
        print(f"events: published {len(events) - failed}/{len(events)}")
        for table, (sent, bad) in sync_tables(rails.store.conn, mirror).items():
            print(f"{table}: published {sent - bad}/{sent}")
    print(f"{'table':16} {'postgres':>9} {'spacetime':>10}")
    print(f"{'events':16} {len(events):>9} {mirror.count():>10}")
    for pg_table, (st_table, *_rest) in TABLES.items():
        if _exists(rails.store.conn, pg_table):
            n = rails.store.conn.execute(f"SELECT count(*) FROM {pg_table}").fetchone()[0]
            print(f"{pg_table:16} {n:>9} {mirror.count(st_table):>10}")


if __name__ == "__main__":
    main()
