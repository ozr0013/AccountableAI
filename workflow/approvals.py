"""Controller approvals over the event log (APPROVALS=real). Agents propose; people decide.

Every request and decision is an append-only event. The rails policy gate enforces the rules:
the proposer can never approve, only a controller can approve, and only a controller can place
a hold. Nothing here moves money; an approved hold is a recorded instruction for a human.
"""

from __future__ import annotations

from datetime import UTC, datetime

from contracts import registry
from contracts.models import Case, Event


def _now() -> datetime:
    return datetime.now(UTC)


class RailsApprovals:
    def __init__(self, rails=None):
        self.rails = rails or registry.rails()

    def _events(self, type_: str) -> list[Event]:
        return [e for e in self.rails.events() if e.type == type_]

    def request(self, case: Case) -> str:
        aid = f"appr-{case.id}"
        if any(e.payload.get("approval_id") == aid for e in self._events("approval_requested")):
            return aid                                  # idempotent: replay asks once
        when = _now()
        self.rails.append_event(Event(
            id=f"ev-{aid}-requested", type="approval_requested", manager_id=case.manager_id,
            valid_from=when, learned_at=when,
            payload={"approval_id": aid, "case": case.model_dump(mode="json")}))
        return aid

    def pending(self) -> list[str]:
        decided = {e.payload["approval_id"] for e in self._events("approval_decided")}
        return [e.payload["approval_id"] for e in self._events("approval_requested")
                if e.payload["approval_id"] not in decided]

    def decide(self, approval_id: str, approver: str, approve: bool) -> Case:
        req = [e for e in self._events("approval_requested")
               if e.payload["approval_id"] == approval_id]
        if not req:
            raise KeyError(f"no approval request {approval_id}")
        if approval_id not in self.pending():
            raise ValueError(f"{approval_id} was already decided")
        case = Case(**req[-1].payload["case"])
        if approve:
            for action in ("approve", "hold"):
                ok, why = self.rails.policy_check(action, approver,
                                                  {"proposed_by": case.proposed_by})
                if not ok:
                    raise PermissionError(why)
            case = case.model_copy(update={"approved_by": approver})
        when = _now()
        self.rails.append_event(Event(
            id=f"ev-{approval_id}-decided", type="approval_decided", manager_id=case.manager_id,
            valid_from=when, learned_at=when,
            payload={"approval_id": approval_id, "approver": approver, "approved": approve,
                     "case": case.model_dump(mode="json")}))
        return case


def build() -> RailsApprovals:
    return RailsApprovals()
