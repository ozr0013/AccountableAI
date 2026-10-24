"""The workflow's own history, kept as append-only events in the rails event log.

Every workflow output (alert raised, alert rejected, chase sent/held, event processed) is an
Event with a deterministic id and the triggering event's timestamps, so replaying the same
input produces byte-identical events and the log never grows duplicates.
`WorkflowBook.load(rails)` folds those events back into current state.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from contracts.models import Alert, ChaseMessage, Event

WF_TYPES = {"workflow_done", "alert_raised", "alert_rejected", "alert_withdrawn",
            "chase_sent", "chase_held", "case_proposed", "terms_proposed", "bank_change_held", "reply_read",
            "agent_decision"}


def digest(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def alert_id(kind: str, manager_id: str, invoice_ids: list[str]) -> str:
    return f"al-{kind}-{digest(manager_id, *sorted(invoice_ids))}"


def wf_event(kind: str, key: str, trigger: Event, payload: dict,
             manager_id: str | None = None, invoice_id: str | None = None) -> Event:
    """A workflow output event. Same (kind, key, trigger) -> same event, always."""
    return Event(
        id=f"ev-wf-{kind}-{digest(key, trigger.id)}",
        type=kind, manager_id=manager_id, invoice_id=invoice_id,
        valid_from=trigger.learned_at, learned_at=trigger.learned_at,
        payload={**payload, "trigger": trigger.id},
    )


def done_event(trigger: Event, summary: dict) -> Event:
    return wf_event("workflow_done", trigger.id, trigger, summary, trigger.manager_id,
                    trigger.invoice_id)


@dataclass
class WorkflowBook:
    processed: set[str] = field(default_factory=set)
    clock: datetime | None = None          # latest moment the workflow has already acted on
    alerts: dict[str, Alert] = field(default_factory=dict)          # every alert ever raised
    open_ids: set[str] = field(default_factory=set)
    chases: dict[str, list[ChaseMessage]] = field(default_factory=dict)  # manager -> sent, oldest first

    @classmethod
    def load(cls, rails) -> WorkflowBook:
        book = cls()
        for e in rails.events():
            if e.type not in WF_TYPES:
                continue
            p = e.payload
            if e.type == "workflow_done":
                book.processed.add(p["trigger"])
                book.clock = max(book.clock or e.learned_at, e.learned_at)
            elif e.type == "alert_raised":
                a = Alert(**{k: p[k] for k in ("draft", "verdict", "state", "withdrawn_reason")})
                book.alerts[a.draft.id] = a
                book.open_ids.add(a.draft.id)
            elif e.type == "alert_withdrawn":
                book.open_ids.discard(p["alert_id"])
                if a := book.alerts.get(p["alert_id"]):
                    book.alerts[p["alert_id"]] = a.model_copy(
                        update={"state": "withdrawn", "withdrawn_reason": p.get("reason")})
            elif e.type == "chase_sent":
                msg = ChaseMessage(**p["chase"])
                book.chases.setdefault(msg.manager_id, []).append(msg)
        return book

    def open_alerts(self, manager_id: str | None = None) -> list[Alert]:
        return [self.alerts[i] for i in sorted(self.open_ids)
                if manager_id in (None, self.alerts[i].draft.manager_id)]
