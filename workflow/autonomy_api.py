"""Authenticated local control plane; routes never call a language model or mail transport."""

from __future__ import annotations

import hmac
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from rails.cases import aware
from workflow.cases import CaseConflict, CaseStore
from workflow.feedback import FeedbackStore
from workflow.human import HumanApprovals, Principal
from workflow.outbox import Outbox
from workflow.runtime_lock import runtime_lock
from workflow.scheduler import Scheduler


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DecideBody(Body):
    approve: bool
    digest: str = Field(min_length=1)


class FeedbackBody(Body):
    case_id: str
    decision_id: str
    text: str = Field(min_length=1, max_length=6000)
    key: str = Field(min_length=1)


class CandidateBody(Body):
    feedback_id: str
    skill_name: str
    version: str
    text: str = Field(min_length=1, max_length=6000)
    capabilities: list[str]
    key: str = Field(min_length=1)


class EvaluationBody(Body):
    case_ids: list[str] = Field(min_length=1, max_length=20)
    key: str = Field(min_length=1)


class ActionBody(Body):
    key: str = Field(min_length=1)


def _principal(authorization: str | None = Header(default=None)):
    expected = os.getenv("CONTROLLER_API_TOKEN")
    if not expected:
        raise HTTPException(503, "controller authentication is not configured")
    if authorization is None or not hmac.compare_digest(
        authorization.encode(), ("Bearer " + expected).encode()
    ):
        raise HTTPException(401, "valid controller bearer token required")
    return Principal("controller.dana", "controller")


def _call(fn):
    try:
        return fn()
    except (KeyError, StopIteration) as exc:
        raise HTTPException(404, "record not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except CaseConflict as exc:
        raise HTTPException(409, "case version changed") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def create_router(rails_provider: Callable, clock=None) -> APIRouter:
    clock = clock or (lambda: datetime.now(UTC))
    router = APIRouter(prefix="/autonomy", dependencies=[Depends(_principal)])

    def now():
        return aware(clock())

    @router.get("/cases")
    def cases(manager_id: str | None = None):
        return _call(lambda: CaseStore(rails_provider()).list(manager_id))

    @router.get("/cases/{case_id}")
    def case(case_id: str):
        return _call(lambda: CaseStore(rails_provider()).get(case_id))

    @router.get("/cases/{case_id}/events")
    def case_events(case_id: str):
        def action():
            rails = rails_provider()
            case = CaseStore(rails).get(case_id)
            return [e for e in rails.events(case.manager_id) if e.payload.get("case_id") == case_id]

        return _call(action)

    @router.post("/cases/{case_id}/wake")
    def wake(case_id: str, body: ActionBody):
        return _call(
            lambda: {
                "job_id": Scheduler(rails_provider()).schedule(
                    case_id, now(), "controller-wake:" + body.key
                )
            }
        )

    @router.get("/jobs")
    def jobs(case_id: str | None = None):
        return _call(lambda: Scheduler(rails_provider()).list(case_id))

    @router.get("/approvals")
    def approvals(case_id: str | None = None):
        return _call(lambda: HumanApprovals(rails_provider()).list(case_id))

    @router.get("/outbox")
    def outbox(case_id: str | None = None):
        return _call(lambda: Outbox(rails_provider()).list(case_id))

    @router.post("/approvals/{ticket_id}/decide")
    def decide(
        ticket_id: str, body: DecideBody, principal: Annotated[Principal, Depends(_principal)]
    ):
        def action():
            rails = rails_provider()
            when = now()
            with runtime_lock(rails, "approval:" + ticket_id):
                approvals = HumanApprovals(rails)
                ticket = approvals.get(ticket_id)
                if body.approve and ticket["kind"] == "case_review":
                    from agents.supervisor import Supervisor

                    case = CaseStore(rails).get(ticket["case_id"])
                    proof = Supervisor(rails)._proof(case, when)
                    if (
                        not ticket["payload"].get("proof_fingerprint")
                        or ticket["payload"]["proof_fingerprint"] != proof["proof_fingerprint"]
                    ):
                        raise ValueError("review proposal must bind current payment proof")
                ticket = approvals.decide(ticket_id, principal, body.approve, when, body.digest)
                if body.approve and ticket["kind"] == "send_message":
                    Outbox(rails).enqueue(
                        ticket["case_id"],
                        **ticket["payload"],
                        now=when,
                        key=ticket_id + ":approved",
                        approval_id=ticket_id,
                    )
                if body.approve and ticket["kind"] == "case_review":
                    CaseStore(rails).record(
                        ticket["case_id"],
                        "human_anomaly_acknowledged",
                        {
                            "proof_fingerprint": ticket["payload"]["proof_fingerprint"],
                            "authorized": True,
                            "actor": principal.id,
                        },
                        when,
                        ticket_id + ":acknowledged",
                    )
                if body.approve and ticket["kind"] == "agreement_terms":
                    from workflow.agreement_intake import confirm_agreement
                    confirm_agreement(rails, ticket, principal, when)
                case = CaseStore(rails).get(ticket["case_id"])
                if case.pending_approval == ticket_id:
                    CaseStore(rails).update(case.id, case.version, when, pending_approval=None)
                Scheduler(rails).schedule(
                    ticket["case_id"],
                    when,
                    ticket_id + (":approved" if body.approve else ":rejected"),
                )
                return ticket

        return _call(action)

    @router.get("/feedback")
    def feedback_list(case_id: str | None = None):
        return _call(lambda: FeedbackStore(rails_provider()).list(case_id))

    @router.post("/feedback")
    def feedback(body: FeedbackBody):
        return _call(
            lambda: FeedbackStore(rails_provider()).feedback(**body.model_dump(), now=now())
        )

    @router.post("/skills/candidates")
    def candidate(body: CandidateBody):
        return _call(
            lambda: FeedbackStore(rails_provider()).candidate(**body.model_dump(), now=now())
        )

    @router.post("/skills/candidates/{candidate_id}/evaluations")
    def evaluation(candidate_id: str, body: EvaluationBody):
        return _call(
            lambda: FeedbackStore(rails_provider()).run_evaluation(
                candidate_id, **body.model_dump(), now=now()
            )
        )

    @router.post("/skills/candidates/{candidate_id}/promote")
    def promote(
        candidate_id: str, body: ActionBody, principal: Annotated[Principal, Depends(_principal)]
    ):
        return _call(
            lambda: FeedbackStore(rails_provider()).promote(
                candidate_id, principal, now(), body.key
            )
        )

    @router.get("/skills/{skill_name}/active")
    def active(skill_name: str):
        return _call(lambda: FeedbackStore(rails_provider()).active_override(skill_name))

    @router.post("/skills/{skill_name}/rollback")
    def rollback(
        skill_name: str, body: ActionBody, principal: Annotated[Principal, Depends(_principal)]
    ):
        return _call(
            lambda: FeedbackStore(rails_provider()).rollback(skill_name, principal, now(), body.key)
        )

    return router


def create_app(rails, *, clock=None) -> FastAPI:
    app = FastAPI(title="Autonomous case control plane")
    app.include_router(create_router(lambda: rails, clock))
    return app
