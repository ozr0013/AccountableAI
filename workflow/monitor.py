"""Single-writer database poller: new facts immediately, a full sweep once per UTC day.

    RAILS=real AGENTS=real VERIFIER=real python -m workflow.monitor --clock world --once
    RAILS=real AGENTS=real VERIFIER=real python -m workflow.monitor --clock wall

Use world time for the seeded demo, wall time for a real daily worker. Run one worker only;
do not run alongside another API process writing workflow outputs to the same database.
The event log provides durable completion and retry state. No email or money is sent.
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from contracts.models import Event

from .book import WorkflowBook
from .runner import Workflow
from .state import Route
from .steps import Deps, Steps

log = logging.getLogger(__name__)


class Monitor:
    def __init__(self, workflow: Workflow):
        self.workflow = workflow

    def tick(self, now: datetime) -> list:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("monitor clock must be timezone aware")
        now = now.astimezone(UTC)
        rails = self.workflow.deps.rails
        book = WorkflowBook.load(rails)
        inputs = [e for e in rails.store.events(current_only=True)
                  if e.id not in book.processed and e.learned_at <= now
                  and Steps._route_for(e) != Route.ignore]
        results = [self.workflow.handle(e) for e in sorted(inputs, key=lambda e: (e.learned_at, e.id))]
        managers = sorted({d.manager_id for d in rails.documents() if d.manager_id})
        for manager in managers:
            eid = f"ev-monitor-scan-{manager}-{now.date().isoformat()}"
            # Reuse the recorded scan after a crash, including its original timestamps.
            event = rails.store.get(eid) or Event(
                id=eid, type="scan_requested", manager_id=manager,
                valid_from=now, learned_at=now, payload={"as_of": now.date().isoformat()})
            if eid not in WorkflowBook.load(rails).processed:
                results.append(self.workflow.ingest(event))
        return results


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clock", choices=("wall", "world"), default="world")
    ap.add_argument("--interval", type=float, default=30,
                    help="seconds between polls; daily scans are deduplicated in the database")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--autonomous", action="store_true",
                    help="Run the agreement, chaser and anomaly agents on durable scheduled jobs")
    args = ap.parse_args()
    if args.interval <= 0:
        ap.error("--interval must be positive")
    load_dotenv(Path.cwd() / ".env", override=False)
    from contracts import registry
    if os.getenv("RAILS", "fake").lower() != "real":
        ap.error("monitor requires RAILS=real (a persistent database)")
    rails = registry.rails()
    if args.autonomous:
        from agents.llm import default_llm

        from .autonomy import AutonomyRuntime
        monitor = AutonomyRuntime(rails, llm=default_llm())
    else:
        monitor = Monitor(Workflow(Deps.from_registry(rails)))
    logging.basicConfig(level=logging.INFO)
    while True:
        now = (datetime.now(UTC) if args.clock == "wall" else
               datetime.combine(rails.now(), datetime.max.time(), tzinfo=UTC))
        try:
            results = monitor.tick(now)
            if results:
                log.info("Processed %d events", len(results))
        except Exception:
            log.exception("Monitor tick failed; unfinished events will retry")
            if args.once:
                raise
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
