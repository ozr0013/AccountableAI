"""Shared data shapes. Everyone builds against these, not against each other's code.

Additive only after Phase 0: add optional fields, never rename or delete.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Sources and citations
# ---------------------------------------------------------------------------


class SourceKind(str, Enum):
    funding = "funding"                  # Oakline -> manager funding record
    manager_statement = "manager_statement"
    manager_bank = "manager_bank"        # manager's bank records
    vendor_invoice = "vendor_invoice"
    vendor_confirmation = "vendor_confirmation"  # vendor says paid / not received
    vendor_notice = "vendor_notice"      # e.g. past-due notice
    agreement = "agreement"              # management agreement clause
    manager_reply = "manager_reply"      # reply to a chase
    controller = "controller"            # controller action / approval
    judge = "judge"                      # Judge Console injected fact


class Citation(BaseModel):
    """Pointer to the exact record backing a claim. Every claim needs >= 1."""

    source_id: str                       # id of a Document or Event
    kind: SourceKind
    locator: str | None = None           # clause "7.2", line "L14", page, etc.
    quote: str | None = None             # short verbatim excerpt


class Document(BaseModel):
    id: str
    kind: SourceKind
    manager_id: str | None = None
    vendor_id: str | None = None
    dated: date | None = None
    text: str = ""
    data: dict = Field(default_factory=dict)  # structured fields when available


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class Manager(BaseModel):
    id: str                              # "keyway"
    name: str                            # "Keyway Property Management"


class Vendor(BaseModel):
    id: str
    name: str
    trade: str                           # plumber, electrician, hvac, ...


# ---------------------------------------------------------------------------
# Event log (append-only, two timelines)
# ---------------------------------------------------------------------------


class Event(BaseModel):
    id: str
    type: str                            # "document_ingested", "fact_corrected", "chase_reply", ...
    manager_id: str | None = None
    invoice_id: str | None = None
    valid_from: datetime                 # when it was true in the world
    learned_at: datetime                 # when we found out
    supersedes: str | None = None        # event id this corrects
    payload: dict = Field(default_factory=dict)
    source: Citation | None = None


# ---------------------------------------------------------------------------
# Agreement terms (Agreement agent output)
# ---------------------------------------------------------------------------


class Term(BaseModel):
    value: int | float | str | bool
    citation: Citation


class AgreementTerms(BaseModel):
    manager_id: str
    vendor_payment_days: Term            # pay vendor within N days of invoice receipt
    holdback_pct: Term | None = None     # e.g. 10 (% held until inspection)
    holdback_condition: Term | None = None
    approval_limit: Term                 # $ above which Oakline must approve/fund first
    allowed_charges: list[Term] = Field(default_factory=list)
    confirmed_by_human: bool = False


# ---------------------------------------------------------------------------
# Ledger / payment trail
# ---------------------------------------------------------------------------


class InvoiceStatus(str, Enum):
    unfunded = "unfunded"
    funded = "funded"
    paid = "paid"
    partially_paid = "partially_paid"
    disputed = "disputed"


class LedgerLine(BaseModel):
    """One vendor invoice, from 'Oakline funded it' to 'vendor confirmed paid'."""

    invoice_id: str
    manager_id: str
    vendor_id: str
    amount: float
    invoice_received: date | None = None
    funded_amount: float = 0.0
    funded_on: date | None = None
    paid_amount: float = 0.0
    paid_on: date | None = None
    due_date: date | None = None         # from confirmed terms
    status: InvoiceStatus = InvoiceStatus.funded
    duplicate_of: str | None = None      # scenario 8


class TrailEntry(BaseModel):
    on: date | None
    kind: SourceKind
    claim: str                           # "paid", "not received", "no payment found", ...
    citation: Citation


class PaymentTrail(BaseModel):
    """Payment-trail agent output: one timeline per invoice + which source wins."""

    invoice_id: str
    entries: list[TrailEntry]
    conflict: bool
    winning_claim: str                   # "unpaid" | "paid" | ...
    winning_sources: list[Citation]
    reasoning: str


# ---------------------------------------------------------------------------
# Delay cause (Delay-cause agent output)
# ---------------------------------------------------------------------------


class Cause(str, Enum):
    authorized_hold = "authorized_hold"
    paperwork_pending = "paperwork_pending"
    vendor_side = "vendor_side"
    owner_side = "owner_side"
    unexplained = "unexplained"


class CauseFinding(BaseModel):
    invoice_id: str
    cause: Cause
    confidence: float = Field(ge=0, le=1)
    explanation: str
    citations: list[Citation]


# ---------------------------------------------------------------------------
# Alerts, verifier, cases
# ---------------------------------------------------------------------------


class AlertKind(str, Enum):
    unexplained_unpaid = "unexplained_unpaid"
    recurring_pattern = "recurring_pattern"
    duplicate_billing = "duplicate_billing"
    bank_detail_change = "bank_detail_change"
    owner_pending_approval = "owner_pending_approval"  # flag on Oakline, not the manager
    paperwork_request = "paperwork_request"


class Claim(BaseModel):
    text: str
    citations: list[Citation]


class AlertDraft(BaseModel):
    id: str
    kind: AlertKind
    manager_id: str
    invoice_ids: list[str]
    amount: float = 0.0
    headline: str
    claims: list[Claim]
    recommended_action: str | None = None


class VerdictStatus(str, Enum):
    approved = "approved"                # survives refutation, all claims cited
    blocked = "blocked"                  # uncited claim or accusatory wording
    refuted = "refuted"                  # evidence contradicts the alert


class Verdict(BaseModel):
    alert_id: str
    status: VerdictStatus
    reasons: list[str]
    uncited_claims: list[str] = Field(default_factory=list)
    banned_terms: list[str] = Field(default_factory=list)
    counter_evidence: list[Citation] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)  # model, tokens, latency_ms (for the scorer)


class Alert(BaseModel):
    draft: AlertDraft
    verdict: Verdict
    state: Literal["open", "withdrawn", "resolved"] = "open"
    withdrawn_reason: str | None = None


class Case(BaseModel):
    """Case agent output: evidence pack + proposed hold for the controller."""

    id: str
    manager_id: str
    alerts: list[str]                    # alert ids
    proposed_hold: float
    summary: str
    evidence: list[Citation]
    proposed_by: str = "case_agent"
    approved_by: str | None = None       # must differ from proposed_by


# ---------------------------------------------------------------------------
# Chasing
# ---------------------------------------------------------------------------


class ChaseMessage(BaseModel):
    id: str
    manager_id: str
    invoice_ids: list[str]
    template_id: str                     # must be a controller-approved template
    level: Literal["polite", "firm", "escalation"]
    body: str
    sent_at: datetime | None = None      # None = drafted, not sent


class ChaseReply(BaseModel):
    id: str
    manager_id: str
    invoice_ids: list[str]
    text: str
    received_at: datetime
    claims_paid: bool
    proof: list[Citation] = Field(default_factory=list)  # vendor or bank proof only counts


# ---------------------------------------------------------------------------
# Briefing
# ---------------------------------------------------------------------------


class Briefing(BaseModel):
    manager_id: str
    question: str
    answer: str
    claims: list[Claim]
    prior_rulings: list[str] = Field(default_factory=list)
