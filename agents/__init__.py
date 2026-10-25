"""Judgment agents. contracts.registry loads build() when AGENTS=real.

Three agents read human text with a model: agreement, chaser (replies) and anomaly (free-text
statements). Trail, cause and case are deterministic code steps the chaser relies on; no model.
All sit behind one facade implementing contracts.steps.Agents.
Keep this module import-light: agents.verifier / agents.briefing import it as their parent.
"""

from __future__ import annotations

from contracts.models import (
    AgreementTerms,
    Alert,
    AnomalyReport,
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
        from contracts.fakes import FakeAgents

        from .agreement import AgreementAgent
        from .anomaly import AnomalyAgent
        from .chaser import ChaserAgent
        self._stand_in = FakeAgents()
        self.agreement = AgreementAgent(llm)
        self.chaser = ChaserAgent(llm, names) if names else ChaserAgent(llm)
        self.anomaly = AnomalyAgent(llm)

    def set_names(self, names) -> None:
        """Let the workflow supply real manager / vendor names for reminder templates."""
        self.chaser.names = names

    def extract_terms(self, agreement: Document) -> AgreementTerms:
        return self.agreement.extract_terms(agreement)

    def build_trail(self, line: LedgerLine, docs: list[Document]) -> PaymentTrail:
        from .trail import build_trail
        return build_trail(line, docs)

    def classify_cause(self, line: LedgerLine, trail: PaymentTrail,
                       terms: AgreementTerms) -> CauseFinding:
        from .cause import classify
        return classify(line, trail, terms)

    def draft_chase(self, manager_id: str, alerts: list[Alert],
                    prior: list[ChaseMessage]) -> ChaseMessage:
        return self.chaser.draft_chase(manager_id, alerts, prior)

    def read_reply(self, reply_text: str, manager_id: str, docs: list[Document]) -> ChaseReply:
        return self.chaser.read_reply(reply_text, manager_id, docs)

    def build_case(self, manager_id: str, alerts: list[Alert]) -> Case:
        from .case import build_case
        return build_case(manager_id, alerts)

    def detect_anomalies(self, statement: Document, ledger: list[LedgerLine]) -> AnomalyReport:
        return self.anomaly.detect_anomalies(statement, ledger)


def build() -> RealAgents:
    from .llm import default_llm
    return RealAgents(default_llm())


__all__ = ["RealAgents", "build"]
