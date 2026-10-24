"""Live Claude agents for the dashboard session, metered against an estimated spending threshold.

The demo world is always replayed in rules mode (free). Switching live on only changes how the
*next* events are handled: Kanishka's agents call Sonnet (structured output) and the verifier
calls its model stage. Usage is estimated after each response; once the threshold is reached, further calls
fall back to rules. A final call can exceed the threshold, and provider billing may differ.
"""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger(__name__)

PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0),
          "claude-haiku-5-5": (0.10, 0.50)}


class Meter:
    def __init__(self, limit_usd: float):
        self.limit, self.spent, self.calls = limit_usd, 0.0, 0
        self.log: list[dict] = []          # newest last: {who, model, cost, in, out}
        self._lock = threading.Lock()

    @property
    def left(self) -> float:
        return max(0.0, self.limit - self.spent)

    def ok(self) -> bool:
        return self.spent < self.limit

    def add(self, who: str, model: str, tin: int, tout: int) -> float:
        pin, pout = PRICES.get(model, PRICES.get(model.rsplit("-", 1)[0], (4.0, 20.0)))
        c = (tin * pin + tout * pout) / 1e6
        with self._lock:
            self.spent += c
            self.calls += 1
            self.log.append({"who": who, "model": model, "cost": round(c, 5),
                             "input_tokens": tin, "output_tokens": tout})
        return c

    def to_json(self) -> dict:
        return {"limit": self.limit, "spent": round(self.spent, 4), "calls": self.calls,
                "recent": self.log[-12:]}


class MeteredLLM:
    """agents.llm.StructuredLLM: (schema, system, user) -> instance | None, priced and capped.

    Calls go through Kanishka's agents.llm.AnthropicLLM, so Claude's summarized thinking, tokens
    and latency reach the workflow's decision records (and the dashboard's live stream)."""

    def __init__(self, meter: Meter, model: str | None = None):
        from agents.llm import AnthropicLLM
        self.inner = AnthropicLLM(model or os.getenv("AGENT_MODEL", "claude-sonnet-5-5"))
        self.model = self.inner.model
        self.meter, self.cache = meter, {}

    def __call__(self, schema, system, user):
        from agents.llm import report_call
        key = (schema.__name__, system, user)
        if key in self.cache:
            out, meta = self.cache[key]
            report_call(**{**meta, "ms": 0, "cached": True})     # same answer, shown as reused
            return out
        if not self.meter.ok():
            return None                     # budget spent: the agent uses its rules
        out = self.inner(schema, system, user)
        meta = dict(self.inner.last)
        if meta.get("input_tokens") is not None:
            self.meter.add(schema.__name__, meta.get("model") or self.model,
                           meta["input_tokens"], meta.get("output_tokens") or 0)
        if out is not None:
            self.cache[key] = (out, meta)
        return out


def available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
