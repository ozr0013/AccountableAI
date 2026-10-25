"""LiveSystem: facts go in, the workflow reacts by itself, every action is on the record.

    live = LiveSystem()                       # real rails; agents/verifier from AGENTS / VERIFIER
    live.warm()                               # process everything already in the log
    live.inject_text("Keyway says they paid the electrician")
    live.advance_clock()                      # jump to the next deadline; reminders follow
    live.pending_approvals(); live.decide(...)  # the only human touch

Runs on RealRails (Postgres). One lock serialises every change, so run a single API process.
The clock is the demo clock: `clock_advanced` events move rails.now() forward, like a test
clock. Reminders are recorded as issued; nothing is emailed.
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, time, timedelta

from contracts import registry
from contracts.models import Event

from ..console import JudgeConsole
from ..real import RealRails
from . import feed as _feed
from .cache import AnswerCache, CachedAgents, CachedVerifier
from .deadlines import next_deadline


class LiveSystem:
    def __init__(self, rails: RealRails | None = None, agents=None, verifier=None, approvals=None,
                 reuse_answers: bool = True):
        """reuse_answers: remember model answers to identical questions (see rails/live/cache.py)
        so a time jump only costs what is new. Off, every question goes to the model again."""
        from workflow import Deps, Workflow
        from workflow.approvals import RailsApprovals
        from workflow.book import WorkflowBook
        self._Book = WorkflowBook
        self.rails = rails or RealRails()
        self.approvals = approvals or RailsApprovals(self.rails, clock=self._stamp)
        agents, verifier = agents or registry.agents(), verifier or registry.verifier()
        # The answer cache is a PostgreSQL table; with the books in SpacetimeDB there is none.
        conn = getattr(self.rails.store, "conn", None)
        self.cache = AnswerCache(conn) if reuse_answers and conn is not None else None
        if self.cache:
            agents, verifier = CachedAgents(agents, self.cache), CachedVerifier(verifier, self.cache)
        self.deps = Deps(self.rails, agents, verifier, self.approvals)
        self.wf = Workflow(self.deps)
        self.console = JudgeConsole(self.rails)
        self.lock = threading.RLock()        # one workflow run at a time
        self._rec = threading.RLock()        # appending facts to the log; never waits on a run

    def _stamp(self) -> datetime:
        """Events made by the live loop are stamped inside the demo day, never the wall clock."""
        return datetime.combine(self.rails.now(), time(12), tzinfo=UTC)

    # -- processing -----------------------------------------------------------------------
    def _summary(self, results) -> list[dict]:
        return [{"event_id": r.event_id, "route": r.route, "skipped": r.skipped,
                 "raised": r.raised, "withdrawn": r.withdrawn, "rejected": r.rejected,
                 "chases": r.chases, "trace": r.trace} for r in results]

    def process(self, events: list[Event]) -> list[dict]:
        with self.lock:
            return self._summary([self.wf.ingest(e) for e in
                                  sorted(events, key=lambda e: (e.learned_at, e.id))])

    def warm(self) -> dict:
        """Process everything already in the log (a no-op for events done before), then one scan.
        Do this once before a demo; the rest is live."""
        from workflow.run import scan_events
        from workflow.state import Route
        from workflow.steps import Steps
        with self.lock:
            # Only events that route somewhere; the workflow's own outputs are not inputs.
            events = [e for e in self.rails.store.events(current_only=True)
                      if Steps._route_for(e) != Route.ignore]
            done = [self.wf.handle(e) for e in events]
            scans = scan_events(sorted(self.console.managers()), self.rails.now())
            done += [self.wf.ingest(e) for e in scans]
            return {"events": len(events), "processed": sum(1 for r in done if not r.skipped)}

    # -- facts in -------------------------------------------------------------------------
    # Each fact has two phases. record_* appends it to the log (quick, always safe) and says
    # what it touches; process() runs the workflow on it (can take minutes with live models,
    # one at a time). inject_* / request_funding / advance_clock do both.
    def record_preset(self, preset_id: str, params: dict | None = None):
        with self._rec:
            return self.console.apply_preset(preset_id, params)

    def record_text(self, text: str, manager_id: str | None = None):
        with self._rec:
            return self.console.apply_text(text, manager_id)

    def record_funding(self, manager_id: str, amount: float) -> Event:
        from workflow.run import funding_event
        with self._rec:
            ev = funding_event(manager_id, amount, self.rails.now())
            self.rails.append_event(ev)
            return ev

    def record_clock(self, days: int | None = None, to: date | None = None):
        """Move the demo clock. Default: to the next deadline (a reminder due or an invoice
        becoming overdue), so the jump always makes something happen. Returns
        (events to process, target date, reason)."""
        from workflow.run import scan_events
        with self._rec:
            today = self.rails.now()
            reason = "demo clock"
            if to:
                target = to
            elif days:
                target = today + timedelta(days=days)
            else:
                nxt = next_deadline(self.rails, self._Book.load(self.rails), today)
                target, reason = nxt if nxt else (today + timedelta(days=1), "next day")
            if target <= today:
                raise ValueError(f"time only moves forward (now {today}, asked for {target})")
            at = datetime.combine(target, time(6), tzinfo=UTC)
            self.rails.append_event(Event(
                id=f"ev-clock-{target.isoformat()}", type="clock_advanced", valid_from=at,
                learned_at=at, payload={"as_of": target.isoformat(), "reason": reason}))
            scans = scan_events(sorted(self.console.managers()), target)
            for sc in scans:
                self.rails.append_event(sc)
            return scans, target, reason

    def inject_preset(self, preset_id: str, params: dict | None = None, wait: bool = True):
        rec = self.record_preset(preset_id, params)
        return rec, (self.process(rec.events) if wait else None)

    def inject_text(self, text: str, manager_id: str | None = None, wait: bool = True):
        rec = self.record_text(text, manager_id)
        return rec, (self.process(rec.events) if wait else None)

    def request_funding(self, manager_id: str, amount: float, wait: bool = True):
        """Oakline is about to fund `manager_id` again: the case agent proposes a hold."""
        ev = self.record_funding(manager_id, amount)
        return ev, (self.process([ev]) if wait else None)

    def advance_clock(self, days: int | None = None, to: date | None = None, wait: bool = True):
        scans, target, reason = self.record_clock(days, to)
        return scans, (self.process(scans) if wait else None), target, reason

    # -- the human touch ------------------------------------------------------------------
    def pending_approvals(self) -> list[dict]:
        ids = self.approvals.pending()
        reqs = {e.payload["approval_id"]: e for e in self.rails.events()
                if e.type == "approval_requested"}
        proposals = {e.payload.get("approval_id"): e.payload for e in self.rails.events()
                     if e.type == "case_proposed"}
        out = []
        for aid in ids:
            case = reqs[aid].payload["case"]
            prop = proposals.get(aid, {})
            out.append({"approval_id": aid, "manager_id": case["manager_id"],
                        "proposed_hold": case["proposed_hold"], "summary": case["summary"],
                        "recommendation": prop.get("recommendation"),
                        "funding_amount": prop.get("funding_amount"),
                        "alerts": case["alerts"], "evidence": [c["source_id"] for c in case["evidence"]],
                        "requested_at": reqs[aid].learned_at.isoformat(),
                        "proposed_by": case["proposed_by"]})
        return out

    def decide(self, approval_id: str, approver: str, approve: bool) -> dict:
        """Controller decision. The policy gate refuses the proposer, and non-controllers."""
        with self._rec:
            case = self.approvals.decide(approval_id, approver, approve)
            return {"approval_id": approval_id, "approved": approve, "approver": approver,
                    "proposed_hold": case.proposed_hold, "approved_by": case.approved_by}

    # -- what happened --------------------------------------------------------------------
    def feed(self, limit: int = 100) -> list[dict]:
        return _feed.feed(self.rails.events(), limit)

    def stats(self) -> dict:
        s = _feed.stats(self.rails.events())
        book = self._Book.load(self.rails)
        s.update({"today": self.rails.now().isoformat(), "open_alerts": len(book.open_ids),
                  "pending_approvals": len(self.approvals.pending()), "mode": self.mode(),
                  **(self.cache.counts() if self.cache else {})})
        return s

    def mode(self) -> dict:
        import os
        agents = self.deps.agents
        live = getattr(agents, "llm", None) or getattr(getattr(agents, "cause", None), "llm", None)
        verifier = getattr(self.deps.verifier, "_inner", self.deps.verifier)
        return {"agents": "model" if live else type(getattr(agents, "_inner", agents)).__name__,
                "verifier": type(verifier).__name__, "reuses_identical_answers": bool(self.cache),
                "models": {k: os.getenv(k) for k in ("AGENT_MODEL", "VERIFIER_MODEL", "MATCH_MODEL")
                           if os.getenv(k)},
                "reminders": "issued and recorded; nothing is emailed"}
