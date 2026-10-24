"""Workflow entry point: one event in, fixed steps run, results written to the event log.

Replay-safe twice over:
  * an event already marked `workflow_done` is skipped;
  * every output event has a deterministic id, so a crashed-and-resumed run can't duplicate it.
Each event runs on its own LangGraph thread (`event:<id>`); a run that crashed mid-way resumes
from its last checkpoint instead of starting over.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from langgraph.checkpoint.base import BaseCheckpointSaver

from contracts.models import Event

from .book import WorkflowBook
from .graph import build_graph
from .steps import Deps, Steps

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    event_id: str
    skipped: bool = False
    route: str | None = None
    raised: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    withdrawn: list[str] = field(default_factory=list)
    chases: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)


class Workflow:
    def __init__(self, deps: Deps, checkpointer: BaseCheckpointSaver | None = None):
        self.deps = deps
        self.graph = build_graph(Steps(deps), checkpointer)

    def handle(self, event: Event) -> RunResult:
        """Run the workflow for an event that is already in the rails log."""
        if event.id in WorkflowBook.load(self.deps.rails).processed:
            return RunResult(event.id, skipped=True)
        cfg = {"configurable": {"thread_id": f"event:{event.id}"}}
        pending = self.graph.get_state(cfg).next
        out = self.graph.invoke(None if pending else {"event": event}, cfg)
        route = out.get("route")
        return RunResult(
            event.id, route=getattr(route, "value", route), raised=out.get("raised", []),
            rejected=out.get("rejected", []), withdrawn=out.get("withdrawn", []), chases=out.get("chases", []),
            trace=out.get("trace", []))

    def ingest(self, event: Event) -> RunResult:
        """Append the event to the log, then handle it (the live / replay path)."""
        self.deps.rails.append_event(event)
        return self.handle(event)

    def replay(self, events: list[Event]) -> list[RunResult]:
        return [self.ingest(e) for e in sorted(events, key=lambda e: (e.learned_at, e.id))]
