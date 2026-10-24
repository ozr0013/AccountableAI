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
    # Action vocabulary and payload keys: rails/guards/policy.py (authoritative).
    # evals/test_prohibited.py holds every implementation to the gates.


class Agents(Protocol):
    """Kanishka: agents/agreement|chaser; trail, cause and hold are code in workflow/checks."""

    def extract_terms(self, agreement: Document) -> AgreementTerms: ...
    def build_trail(self, line: LedgerLine, docs: list[Document]) -> PaymentTrail: ...
    def classify_cause(
        self, line: LedgerLine, trail: PaymentTrail, terms: AgreementTerms
    ) -> CauseFinding: ...
    def draft_chase(self, manager_id: str, alerts: list[Alert], prior: list[ChaseMessage]) -> ChaseMessage: ...
    def read_reply(self, reply_text: str, manager_id: str, docs: list[Document]) -> ChaseReply: ...
    def build_case(self, manager_id: str, alerts: list[Alert]) -> Case: ...


class Verifier(Protocol):
    """Code checks: workflow/checks/wording.py."""

    def verify(self, draft: AlertDraft, evidence: list[Document]) -> Verdict: ...


class Briefer(Protocol):
    """Code briefing: workflow/checks/briefing.py."""

    def brief(self, manager_id: str, question: str) -> Briefing: ...


class Approvals(Protocol):
    """Controller approvals. Proposer can never approve their own proposal."""

    def request(self, case: Case) -> str: ...                        # returns approval id
    def decide(self, approval_id: str, approver: str, approve: bool) -> Case: ...
