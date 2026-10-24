"""Judgment agents. contracts.registry loads build() when AGENTS=real.

Kanishka's agents (agreement, trail, cause, chaser, case) sit behind one facade implementing
contracts.steps.Agents. Methods whose agent isn't built yet delegate to the stand-in.
Keep this module import-light: agents.verifier / agents.briefing import it as their parent.
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
    def __init__(self, llm=None):
        from contracts.fakes import FakeAgents

        from .trail import TrailAgent
        self._stand_in = FakeAgents()
        self.trail = TrailAgent(llm)

    def extract_terms(self, agreement: Document) -> AgreementTerms:
        return self._stand_in.extract_terms(agreement)

    def build_trail(self, line: LedgerLine, docs: list[Document]) -> PaymentTrail:
        return self.trail.build_trail(line, docs)

    def classify_cause(self, line: LedgerLine, trail: PaymentTrail,
                       terms: AgreementTerms) -> CauseFinding:
        return self._stand_in.classify_cause(line, trail, terms)

    def draft_chase(self, manager_id: str, alerts: list[Alert],
                    prior: list[ChaseMessage]) -> ChaseMessage:
        return self._stand_in.draft_chase(manager_id, alerts, prior)

    def read_reply(self, reply_text: str, manager_id: str, docs: list[Document]) -> ChaseReply:
        return self._stand_in.read_reply(reply_text, manager_id, docs)

    def build_case(self, manager_id: str, alerts: list[Alert]) -> Case:
        return self._stand_in.build_case(manager_id, alerts)


def build() -> RealAgents:
    from .llm import default_llm
    return RealAgents(default_llm())


__all__ = ["RealAgents", "build"]
