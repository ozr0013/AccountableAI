"""Graph state and the small value types the workflow passes between steps."""

from __future__ import annotations

import operator
from datetime import date
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from contracts.models import AlertDraft, CauseFinding, Event, PaymentTrail


class Route(str, Enum):
    agreement = "agreement"            # new agreement -> extract terms
    assess = "assess"                  # documents / corrected facts -> recheck affected invoices
    reply = "reply"                    # manager replied to a chase
    scan = "scan"                      # periodic sweep of one manager
    bank_change = "bank_change"        # vendor asks to change bank details
    funding_review = "funding_review"  # Oakline is about to send more money
    ignore = "ignore"


class Assessment(BaseModel):
    """What the workflow concluded about one invoice at `now`."""

    invoice_id: str
    manager_id: str
    status: Literal["paid", "not_due", "delayed", "skipped"]
    days_late: int = 0
    trail: PaymentTrail | None = None
    finding: CauseFinding | None = None
    note: str = ""


class WorkflowState(BaseModel):
    event: Event
    now: date | None = None
    route: Route | None = None
    manager_id: str | None = None
    invoice_ids: list[str] = Field(default_factory=list)
    assessments: list[Assessment] = Field(default_factory=list)
    drafts: list[AlertDraft] = Field(default_factory=list)
    raised: list[str] = Field(default_factory=list)       # alert ids opened by this run
    refreshed: list[str] = Field(default_factory=list)    # open alerts re-verified on new evidence
    rejected: list[str] = Field(default_factory=list)     # alert ids the verifier stopped
    withdrawn: list[str] = Field(default_factory=list)    # alerts the new facts no longer support
    chases: list[str] = Field(default_factory=list)       # chase ids sent or held
    case_id: str | None = None                            # case proposed to the controller
    trace: Annotated[list[str], operator.add] = Field(default_factory=list)  # the "why" log
