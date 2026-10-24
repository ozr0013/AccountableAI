"""Fixed step signatures. The workflow calls these; owners implement them.

Each group is a Protocol. Real implementations live in the owner's folder;
stand-ins live in contracts/fakes/. Pick one with an env var (see registry.py).
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from .models import (
    AgreementTerms,
    Alert,
    AlertDraft,
    Briefing,
    Case,
    CauseFinding,
    ChaseMessage,
    ChaseReply,
    Document,
    Event,
    LedgerLine,
    PaymentTrail,
    Verdict,
)


class Rails(Protocol):
    """Aaryan: rails/. Deterministic code, no LLM calls."""

    def append_event(self, event: Event) -> None: ...
    def events(self, manager_id: str | None = None) -> list[Event]: ...
    def documents(self, manager_id: str | None = None, invoice_id: str | None = None) -> list[Document]: ...
    def ledger(self, manager_id: str) -> list[LedgerLine]: ...
    def unpaid(self, manager_id: str, as_of: date) -> list[LedgerLine]: ...
    def late_months(self, manager_id: str) -> dict[str, bool]: ...   # "2026-03" -> late?
    def reconcile(self, manager_id: str) -> dict[str, float]: ...    # funded, paid, held, owed, diff
    def dependents_of(self, fact_id: str) -> list[str]: ...          # invoice/alert ids to recheck
    def policy_check(self, action: str, actor: str, payload: dict) -> tuple[bool, str]: ...
    # Actions (evals/test_prohibited.py holds every implementation to these):
    #   move_money, send_unapproved          -> always denied
    #   approve        {proposed_by}         -> denied if proposed_by == actor
    #   send_reminder  {template_approved}   -> denied unless template_approved
    #   update_bank_details {identity_verified, approved_by} -> denied unless both, approver != actor
    #   post_entry     {month_closed}        -> denied if month_closed


class Agents(Protocol):
    """Kanishka: agents/agreement|trail|cause|chaser|case."""

    def extract_terms(self, agreement: Document) -> AgreementTerms: ...
    def build_trail(self, line: LedgerLine, docs: list[Document]) -> PaymentTrail: ...
    def classify_cause(
        self, line: LedgerLine, trail: PaymentTrail, terms: AgreementTerms
    ) -> CauseFinding: ...
    def draft_chase(self, manager_id: str, alerts: list[Alert], prior: list[ChaseMessage]) -> ChaseMessage: ...
    def read_reply(self, reply_text: str, manager_id: str, docs: list[Document]) -> ChaseReply: ...
    def build_case(self, manager_id: str, alerts: list[Alert]) -> Case: ...


class Verifier(Protocol):
    """Omar: agents/verifier/."""

    def verify(self, draft: AlertDraft, evidence: list[Document]) -> Verdict: ...


class Briefer(Protocol):
    """Omar: agents/briefing/."""

    def brief(self, manager_id: str, question: str) -> Briefing: ...


class Approvals(Protocol):
    """Controller approvals. Proposer can never approve their own proposal."""

    def request(self, case: Case) -> str: ...                        # returns approval id
    def decide(self, approval_id: str, approver: str, approve: bool) -> Case: ...
