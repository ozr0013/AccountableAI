"""Shared LLM plumbing for Kanishka's agents: one structured-output Claude call, guarded.

    answer, problems = guarded_call(llm, Schema, system, user, check)

`llm` is any callable (schema, system, user) -> instance | None. The real one is LangChain's
ChatAnthropic with native structured outputs (method="json_schema": Sonnet 5.5 rejects the
forced tool_choice that "function_calling" relies on). Tests pass a scripted fake.
No API key, an API error, or an answer that fails `check` twice -> (None, problems), and the
caller uses its deterministic rules instead. The workflow never stops on an LLM failure.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Protocol, TypeVar

from pydantic import BaseModel

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
DEFAULT_MODEL = "claude-sonnet-5-5"     # README: Sonnet for agreement/trail/cause/chaser/case


class StructuredLLM(Protocol):
    def __call__(self, schema: type[T], system: str, user: str) -> T | None: ...


class AnthropicLLM:
    """ChatAnthropic (LangChain) returning a validated Pydantic object, or None on failure."""

    def __init__(self, model: str | None = None, max_tokens: int = 8000):
        from langchain_anthropic import ChatAnthropic
        self.model = model or os.getenv("AGENT_MODEL", DEFAULT_MODEL)
        self.chat = ChatAnthropic(model=self.model, max_tokens=max_tokens, max_retries=2,
                                  timeout=120)

    def __call__(self, schema: type[T], system: str, user: str) -> T | None:
        try:
            runnable = self.chat.with_structured_output(schema, method="json_schema")
            out = runnable.invoke([("system", system), ("human", user)])
        except Exception as e:      # noqa: BLE001 - any LLM failure falls back to rules
            log.warning("LLM call failed (%s): %s", schema.__name__, e)
            return None
        return out if isinstance(out, schema) else None


def default_llm() -> StructuredLLM | None:
    """The real LLM when credentials exist and AGENTS_LLM isn't "off"; else None (rules only)."""
    if os.getenv("AGENTS_LLM", "on").lower() == "off":
        return None
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        return None
    return AnthropicLLM()


def guarded_call(llm: StructuredLLM | None, schema: type[T], system: str, user: str,
                 check: Callable[[T], list[str]], retries: int = 1) -> tuple[T | None, list[str]]:
    """Ask, check, retry once with the problems spelled out. (answer | None, last problems)."""
    if llm is None:
        return None, ["llm disabled"]
    prompt, problems = user, ["no answer"]
    for _ in range(retries + 1):
        out = llm(schema, system, prompt)
        problems = check(out) if out is not None else ["no answer"]
        if not problems:
            return out, []
        prompt = (f"{user}\n\nYour previous answer was rejected for these reasons:\n- "
                  + "\n- ".join(problems) + "\nAnswer again and fix them.")
    log.info("%s answer rejected, using rules: %s", schema.__name__, problems)
    return None, problems
