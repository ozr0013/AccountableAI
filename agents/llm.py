"""Shared LLM plumbing for Kanishka's agents: one structured-output Claude call, guarded.

    answer, problems = guarded_call(llm, Schema, system, user, check)

`llm` is any callable (schema, system, user) -> instance | None. The real one is LangChain's
ChatAnthropic with native structured outputs (method="json_schema": Sonnet 5.5 rejects the
forced tool_choice that "function_calling" relies on). Tests pass a scripted fake.
No API key, an API error, or an answer that fails `check` twice -> (None, problems), and the
caller uses its deterministic rules instead. The workflow never stops on an LLM failure.

Observability: inside `capture_attempts()`, every model call (model, Claude's summarized
thinking, tokens, latency) and every guard check (accepted / rejected and why) is recorded, so
the workflow can show how each decision was reached. Outside it, nothing is recorded.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol, TypeVar

from pydantic import BaseModel

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
DEFAULT_MODEL = "claude-sonnet-5-5"     # README: Sonnet for agreement/trail/cause/chaser/case
THINKING = {"type": "adaptive", "display": "summarized"}   # a readable summary, never raw

_ATTEMPTS: ContextVar[list[dict] | None] = ContextVar("llm_attempts", default=None)
_THINKING_SINK: ContextVar[Callable[[str], None] | None] = ContextVar("thinking_sink", default=None)


@contextmanager
def stream_thinking(sink: Callable[[str], None]) -> Iterator[None]:
    """While active, Claude is streamed and each piece of its (summarized) thinking is passed to
    sink as it arrives, so a viewer can watch it think. Without it, calls aren't streamed."""
    token = _THINKING_SINK.set(sink)
    try:
        yield
    finally:
        _THINKING_SINK.reset(token)


@contextmanager
def capture_attempts() -> Iterator[list[dict]]:
    """Collect the model calls and guard checks made while the block runs."""
    token = _ATTEMPTS.set([])
    try:
        yield _ATTEMPTS.get()
    finally:
        _ATTEMPTS.reset(token)


def report_call(**meta) -> None:
    """Called by LLM implementations after each request: model, thinking, tokens, ms, error."""
    calls = _ATTEMPTS.get()
    if calls is not None:
        calls.append({"kind": "call", **meta})


class StructuredLLM(Protocol):
    def __call__(self, schema: type[T], system: str, user: str) -> T | None: ...


def _thinking_text(content) -> str:
    if not isinstance(content, list):
        return ""
    parts = [b.get("thinking", "") for b in content if isinstance(b, dict) and b.get("type") == "thinking"]
    return "\n".join(p for p in parts if p).strip()


class AnthropicLLM:
    """ChatAnthropic (LangChain) returning a validated Pydantic object, or None on failure.
    Asks Claude for a summarized view of its thinking and reports it with usage per call."""

    def __init__(self, model: str | None = None, max_tokens: int = 8000, thinking: bool = True):
        from langchain_anthropic import ChatAnthropic
        self.model = model or os.getenv("AGENT_MODEL", DEFAULT_MODEL)
        show = thinking and os.getenv("AGENT_THINKING", "summarized") != "off"
        self.chat = ChatAnthropic(model=self.model, max_tokens=max_tokens, max_retries=2,
                                  timeout=120, thinking=THINKING if show else None)
        self.last: dict = {}            # the latest call's meta (for callers that meter usage)

    def __call__(self, schema: type[T], system: str, user: str) -> T | None:
        start = time.monotonic()
        messages = [("system", system), ("human", user)]
        sink = _THINKING_SINK.get()
        try:
            if sink is not None:
                parsed, raw, error = self._stream(schema, messages, sink)
            else:
                runnable = self.chat.with_structured_output(schema, method="json_schema",
                                                            include_raw=True)
                out = runnable.invoke(messages)
                parsed, raw = out.get("parsed"), out.get("raw")
                error = str(out.get("parsing_error"))[:300] if out.get("parsing_error") else None
        except Exception as e:      # noqa: BLE001 - any LLM failure falls back to rules
            log.warning("LLM call failed (%s): %s", schema.__name__, e)
            self.last = {"model": self.model, "error": f"{type(e).__name__}: {e}"[:300],
                         "ms": int((time.monotonic() - start) * 1000)}
            report_call(**self.last)
            return None
        usage = getattr(raw, "usage_metadata", None) or {}
        meta = getattr(raw, "response_metadata", None) or {}
        self.last = {"model": meta.get("model_name") or meta.get("model") or self.model,
                     "thinking": _thinking_text(getattr(raw, "content", None)),
                     "input_tokens": usage.get("input_tokens"),
                     "output_tokens": usage.get("output_tokens"),
                     "stop_reason": meta.get("stop_reason"),
                     "ms": int((time.monotonic() - start) * 1000), "error": error}
        report_call(**self.last)
        return parsed if isinstance(parsed, schema) else None

    def _stream(self, schema: type[T], messages, sink: Callable[[str], None]):
        """Same request as with_structured_output(method="json_schema"), streamed: thinking
        pieces go to sink as they arrive; the finished JSON is validated against schema."""
        from langchain_anthropic.chat_models import _convert_to_anthropic_output_config_format
        bound = self.chat.bind(output_config={
            "format": _convert_to_anthropic_output_config_format(schema)})
        full = None
        for chunk in bound.stream(messages):
            for block in chunk.content if isinstance(chunk.content, list) else []:
                if isinstance(block, dict) and block.get("type") == "thinking" and block.get("thinking"):
                    sink(block["thinking"])
            full = chunk if full is None else full + chunk
        content = getattr(full, "content", "")
        text = content if isinstance(content, str) else "".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        try:
            return schema.model_validate_json(text), full, None
        except Exception as e:      # noqa: BLE001 - a malformed answer is a rejected answer
            return None, full, f"could not parse the answer: {e}"[:300]


def default_llm() -> StructuredLLM | None:
    """The real LLM when credentials exist and AGENTS_LLM isn't "off"; else None (rules only)."""
    if os.getenv("AGENTS_LLM", "on").lower() == "off":
        return None
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        return None
    return AnthropicLLM()


def _short(answer: BaseModel | None, limit: int = 800) -> str | None:
    if answer is None:
        return None
    text = json.dumps(answer.model_dump(mode="json"), default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def guarded_call(llm: StructuredLLM | None, schema: type[T], system: str, user: str,
                 check: Callable[[T], list[str]], retries: int = 1) -> tuple[T | None, list[str]]:
    """Ask, check, retry once with the problems spelled out. (answer | None, last problems)."""
    if llm is None:
        return None, ["llm disabled"]
    record = _ATTEMPTS.get()
    prompt, problems = user, ["no answer"]
    for attempt in range(1, retries + 2):
        before = len(record) if record is not None else 0
        out = llm(schema, system, prompt)
        problems = check(out) if out is not None else ["no answer"]
        if record is not None:
            calls = [c for c in record[before:] if c.get("kind") == "call"]
            del record[before:]
            record.append({**(calls[-1] if calls else {}), "kind": "attempt",
                           "schema": schema.__name__, "attempt": attempt,
                           "accepted": not problems, "problems": problems, "answer": _short(out)})
        if not problems:
            return out, []
        prompt = (f"{user}\n\nYour previous answer was rejected for these reasons:\n- "
                  + "\n- ".join(problems) + "\nAnswer again and fix them.")
    log.info("%s answer rejected, using rules: %s", schema.__name__, problems)
    return None, problems
