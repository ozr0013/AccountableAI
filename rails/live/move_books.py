"""Move the books from PostgreSQL into SpacetimeDB (one time), then prove nothing changed.

    python -m rails.live.move_books            # copy schema 'public' into book 'public'
    python -m rails.live.move_books --check    # only compare

Events are appended in their original order through the same reducer every later write uses, so
a re-run skips what is already there and refuses anything that would rewrite history. The check
rebuilds the ledger from both stores and compares every line to the cent.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path


def compare(pg, st) -> list[str]:
    from rails.ledger import build_ledger
    problems = []
    a, b = pg.events(), st.events()
    if [e.id for e in a] != [e.id for e in b]:
        problems.append(f"event order differs ({len(a)} in PostgreSQL, {len(b)} in SpacetimeDB)")
    la = {l.invoice_id: l.model_dump(mode="json") for l in build_ledger(pg.events(current_only=True))}
    lb = {l.invoice_id: l.model_dump(mode="json") for l in build_ledger(st.events(current_only=True))}
    for iid in sorted(set(la) | set(lb)):
        if la.get(iid) != lb.get(iid):
            problems.append(f"ledger line {iid} differs")
    return problems


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(Path.cwd() / ".env", override=False)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", default="public", help="PostgreSQL schema to copy")
    ap.add_argument("--book", default=None, help="SpacetimeDB book (default: same as schema)")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    from rails.db import connect
    from rails.events import EventStore
    from rails.events.spacetime_store import SpacetimeEventStore
    pg = EventStore(conn=connect(args.schema))
    st = SpacetimeEventStore(book=args.book or args.schema)
    if not args.check:
        events, new, t0 = pg.events(), 0, time.time()
        for i, e in enumerate(events, 1):
            new += st.append(e)
            if i % 250 == 0:
                print(f"  {i}/{len(events)} events ({time.time() - t0:.0f}s)", flush=True)
        print(f"copied {new} new events ({len(events) - new} already there)")
    problems = compare(pg, st)
    print("identical: every event in order, every ledger line to the cent" if not problems
          else "DIFFERENCES:\n  " + "\n  ".join(problems[:20]))


if __name__ == "__main__":
    main()
