"""Replay a generated world through the workflow and print what it did.

    RAILS=fake AGENTS=fake VERIFIER=fake python -m workflow.run --scenario 3
    python -m workflow.run --scenario 1 3 5 9 --clean 20

RAILS=real uses Postgres (load it first with `python -m rails.load`); anything else replays the
world in memory (workflow/memrails.py), since the fixture stand-in can't replay a timeline.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime, time

from contracts.models import Event
from world import generate

from .book import WorkflowBook
from .memrails import MemoryRails
from .runner import Workflow
from .steps import Deps


def scan_events(manager_ids: list[str], as_of) -> list[Event]:
    when = datetime.combine(as_of, time(23, 0), tzinfo=UTC)
    return [Event(id=f"ev-scan-{m}-{as_of.isoformat()}", type="scan_requested", manager_id=m,
                  valid_from=when, learned_at=when, payload={"as_of": as_of.isoformat()})
            for m in manager_ids]


def funding_event(manager_id: str, amount: float, as_of) -> Event:
    when = datetime.combine(as_of, time(23, 30), tzinfo=UTC)
    return Event(id=f"ev-funding-{manager_id}-{as_of.isoformat()}", type="funding_review_requested",
                 manager_id=manager_id, valid_from=when, learned_at=when,
                 payload={"amount": amount})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", type=int, nargs="*", default=[3])
    ap.add_argument("--clean", type=int, default=30)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--funding", metavar="MANAGER:AMOUNT",
                    help="after the replay, Oakline is about to fund e.g. keyway:18400")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    world = generate(a.seed, tuple(a.scenario), a.clean)
    if os.getenv("RAILS", "fake").lower() == "real":
        from contracts import registry
        rails = registry.rails()
        rails.load(world.events())
        events = rails.store.events(current_only=True)
        wf = Workflow(Deps.from_registry(rails))
        results = [wf.handle(e) for e in events]
    else:
        rails = MemoryRails()
        wf = Workflow(Deps.from_registry(rails))
        results = wf.replay(world.events())
    results += wf.replay(scan_events([m.id for m in world.managers], world.as_of))
    if a.funding:
        mgr, amount = a.funding.split(":")
        results += wf.replay([funding_event(mgr, float(amount), world.as_of)])

    book = WorkflowBook.load(rails)
    for r in results:
        if a.verbose or r.raised or r.rejected or r.chases or r.route == "funding_review":
            print(f"[{r.route}] {r.event_id}")
            for line in r.trace:
                print(f"    {line}")
    print(f"\n{len(results)} events, {len(book.open_ids)} open alert(s):")
    for al in book.open_alerts():
        print(f"  {al.draft.id}  {al.draft.headline}")


if __name__ == "__main__":
    main()
