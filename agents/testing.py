"""Test helpers for agents: a scripted LLM and world fixtures (no network, no Postgres)."""

from __future__ import annotations

from functools import lru_cache

from contracts.models import Document, LedgerLine


class ScriptedLLM:
    """Returns the queued answers in order (a model instance, or None for 'call failed')."""

    def __init__(self, *answers, thinking: str | None = None, model: str = "scripted"):
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.thinking, self.model = thinking, model

    def __call__(self, schema, system, user):
        from agents.llm import report_call
        self.prompts.append(user)
        report_call(model=self.model, thinking=self.thinking or "", input_tokens=100,
                    output_tokens=50, ms=1)
        if not self.answers:
            return None
        a = self.answers.pop(0)
        return schema.model_validate(a) if isinstance(a, dict) else a


@lru_cache(maxsize=1)
def world_rails():
    from workflow.memrails import MemoryRails
    from world.generator import ALL_SCENARIOS
    return MemoryRails.from_world(ALL_SCENARIOS, n_clean=20)


def invoice(invoice_id: str, manager_id: str) -> tuple[LedgerLine, list[Document]]:
    r = world_rails()
    line = next(l for l in r.ledger(manager_id) if l.invoice_id == invoice_id)
    return line, r.documents(manager_id, invoice_id)
