"""Judgment agents. contracts.registry loads build() when AGENTS=real.

Three agents: agreement (reads the contract), chaser (reminders and replies) and anomaly
(checks a payment once it is made; see agents/anomaly). This facade implements
contracts.steps.Agents for the event-by-event workflow: the trail, cause and hold steps there
are plain code in workflow/checks, not agents.
"""

from __future__ import annotations

from contracts.models import (
    AgreementTerms,
    Alert,
    Case,
    CauseFinding,
    ChaseMessage,
    ChaseReply,
    Document,
    LedgerLine,
    PaymentTrail,
)


class RealAgents:
    def __init__(self, llm=None, names=None):
        from .agreement import AgreementAgent
        from .chaser import ChaserAgent
        self.agreement = AgreementAgent(llm)
        self.chaser = ChaserAgent(llm, names) if names else ChaserAgent(llm)

    def set_names(self, names) -> None:
        """Let the workflow supply real manager / vendor names for reminder templates."""
        self.chaser.names = names

    def extract_terms(self, agreement: Document) -> AgreementTerms:
        return self.agreement.extract_terms(agreement)

    def build_trail(self, line: LedgerLine, docs: list[Document]) -> PaymentTrail:
        from workflow.checks.trail import build_trail
        return build_trail(line, docs)

    def classify_cause(self, line: LedgerLine, trail: PaymentTrail,
                       terms: AgreementTerms) -> CauseFinding:
        from workflow.checks.cause import classify
        return classify(line, trail, terms)

    def draft_chase(self, manager_id: str, alerts: list[Alert],
                    prior: list[ChaseMessage]) -> ChaseMessage:
        return self.chaser.draft_chase(manager_id, alerts, prior)

    def read_reply(self, reply_text: str, manager_id: str, docs: list[Document]) -> ChaseReply:
        return self.chaser.read_reply(reply_text, manager_id, docs)

    def build_case(self, manager_id: str, alerts: list[Alert]) -> Case:
        from workflow.checks.hold import build_case
        return build_case(manager_id, alerts)


def build() -> RealAgents:
    from .llm import default_llm
    return RealAgents(default_llm())


__all__ = ["RealAgents", "build"]
