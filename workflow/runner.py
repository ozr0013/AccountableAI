"""Workflow entry point: one event in, fixed steps run, results written to the event log.

Replay-safe twice over:
  * an event already marked `workflow_done` is skipped;
  * every output event has a deterministic id, so a crashed-and-resumed run can't duplicate it.
Each event runs on its own LangGraph thread (`event:<id>`). A run that crashed mid-way resumes
from its last checkpoint; a run paused for a human (terms confirmation) waits until
`resume(event_id, decision)`. WORKFLOW_CHECKPOINT=postgres keeps those threads in Postgres so a
pause survives a restart; the default is in-memory.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from contracts.models import Event

from .book import WorkflowBook
from .graph import build_graph
from .names import names_from
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
    waiting: dict | None = None          # set while paused for a human; what they must decide


def checkpointer_from_env() -> BaseCheckpointSaver | None:
    if os.getenv("WORKFLOW_CHECKPOINT", "memory").lower() != "postgres":
        return None
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver

    from rails.db import database_url

    from .graph import serde
    saver = PostgresSaver(psycopg.connect(database_url(), autocommit=True), serde=serde())
    saver.setup()
    return saver


class Workflow:
    def __init__(self, deps: Deps, checkpointer: BaseCheckpointSaver | None = None):
        self.deps = deps
        if hasattr(deps.agents, "set_names"):       # real chaser renders templates with names
            deps.agents.set_names(names_from(deps.rails))
        self.graph = build_graph(Steps(deps), checkpointer or checkpointer_from_env())

    @staticmethod
    def _cfg(event_id: str) -> dict:
        return {"configurable": {"thread_id": f"event:{event_id}"}}

    def handle(self, event: Event) -> RunResult:
        """Run the workflow for an event that is already in the rails log."""
        if event.id in WorkflowBook.load(self.deps.rails).processed:
            return RunResult(event.id, skipped=True)
        cfg = self._cfg(event.id)
        st = self.graph.get_state(cfg)
        if st.next and st.interrupts:                    # paused for a human: don't re-run
            return RunResult(event.id, route=getattr(st.values.get("route"), "value", None),
                             waiting=st.interrupts[0].value)
        result = self._result(event.id, self.graph.invoke(None if st.next else {"event": event}, cfg))
        if event.type == "terms_confirmed" and event.manager_id:
            self._settle_terms(event.manager_id)
        return result

    def _settle_terms(self, manager_id: str) -> None:
        """Terms confirmed elsewhere: paused proposals that now match close themselves
        (confirm_terms re-checks on resume and only pauses again if they still differ)."""
        for eid in self.waiting():
            v = self.graph.get_state(self._cfg(eid)).values
            if v.get("manager_id") == manager_id and v.get("terms") is not None:
                self.graph.invoke(Command(resume={}), self._cfg(eid))

    def resume(self, event_id: str, decision: dict) -> RunResult:
        """Continue a run paused for a human, e.g. {"approve": True, "by": "controller.dana"}."""
        cfg = self._cfg(event_id)
        if not self.graph.get_state(cfg).next:
            raise ValueError(f"no paused run for {event_id}")
        return self._result(event_id, self.graph.invoke(Command(resume=decision), cfg))

    def waiting(self) -> list[str]:
        """Event ids of runs paused for a human (latest state of each thread)."""
        threads = {c.config["configurable"]["thread_id"] for c in self.graph.checkpointer.list(None)}
        return sorted(t.removeprefix("event:") for t in threads
                      if self.graph.get_state({"configurable": {"thread_id": t}}).interrupts)

    def _result(self, event_id: str, out: dict) -> RunResult:
        route = out.get("route")
        paused = out.get("__interrupt__")
        return RunResult(
            event_id, route=getattr(route, "value", route), raised=out.get("raised", []),
            rejected=out.get("rejected", []), withdrawn=out.get("withdrawn", []),
            chases=out.get("chases", []), trace=out.get("trace", []),
            waiting=paused[0].value if paused else None)

    def ingest(self, event: Event) -> RunResult:
        """Append the event to the log, then handle it (the live / replay path)."""
        self.deps.rails.append_event(event)
        return self.handle(event)

    def replay(self, events: list[Event]) -> list[RunResult]:
        return [self.ingest(e) for e in sorted(events, key=lambda e: (e.learned_at, e.id))]
