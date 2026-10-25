"""Load a generated world into Postgres:  python -m rails.load [--seed 13] [--scenarios 1 2 ... 9 (default: all)] [--fresh]

--fresh drops the rails tables first (the log is append-only, so this is the only reset).
"""

import argparse

from world import generate
from world.generator import ALL_SCENARIOS

from .db import connect
from .real import RealRails


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--clean", type=int, default=100)
    ap.add_argument("--scenarios", type=int, nargs="*", default=list(ALL_SCENARIOS))
    ap.add_argument("--fresh", action="store_true")
    a = ap.parse_args()
    if a.fresh:
        from .real import books_backend
        if books_backend() == "spacetime":
            raise SystemExit("the books in SpacetimeDB are append-only and are never dropped; "
                             "load a new world into a fresh SpacetimeDB database instead")
        connect().execute("DROP TABLE IF EXISTS dependencies, event_facts, events CASCADE")
    rails = RealRails()
    w = generate(a.seed, tuple(a.scenarios), a.clean)
    n = rails.load(w.events())
    print(f"loaded {n} new events (as_of {rails.now()})")


if __name__ == "__main__":
    main()
