"""Mirror the PostgreSQL event log into SpacetimeDB so the dashboard can subscribe live.

PostgreSQL stays the source of truth. Each event is published through the module's
`publish_event` reducer over HTTP; the reducer ignores ids it already has, so a backfill or
a retry never duplicates rows.

    python -m rails.live.spacetime --backfill        # copy every Postgres event
    python -m rails.live.spacetime --check           # compare row counts

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

    def count(self) -> int:
        rows = json.loads(_post("sql", b"SELECT * FROM event", self.token, "text/plain"))
        return len(rows[0]["rows"]) if rows else 0


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
        print(f"published {len(events) - failed}/{len(events)} events")
    print(f"postgres events: {len(events)}  spacetime events: {mirror.count()}")


if __name__ == "__main__":
    main()
