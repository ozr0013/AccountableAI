"""Shared Claude tool loop for the pipeline agents (chaser, anomaly).

An agent is: a system prompt, a skill file (agent guidance, loaded into the system prompt), a set
of tools (JSON schema + Python function) and a task. Claude decides which tools to call; each
tool runs our code, which can refuse an unsafe request, and the result goes back to Claude. The
run ends when Claude calls one of the agent's terminal tools (its decision) or stops.

Every run returns a trace - reasoning summaries, each tool call with its input and result, the
decision and token usage - so the frontend can show exactly what the agent did and why.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

MODEL = os.getenv("PIPELINE_MODEL", "claude-opus-5-5")
MAX_TURNS = 10
RESULT_LIMIT = 8000
SKILLS = Path(__file__).resolve().parent / "skills"


def load_skill(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text()


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    fn: Callable[..., dict]
    terminal: bool = False          # a successful call ends the run: it is the agent's decision

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema}


@dataclass
class Trace:
    agent: str
    model: str = MODEL
    thinking: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    decision: dict | None = None
    text: str = ""
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0,
                                                 "cache_read_input_tokens": 0})
    error: str | None = None

    def to_dict(self) -> dict:
        return {"agent": self.agent, "model": self.model, "thinking": self.thinking,
                "steps": self.steps, "decision": self.decision, "text": self.text,
                "usage": self.usage, "error": self.error}


def _client():
    import anthropic
    return anthropic.Anthropic()


def run_agent(agent: str, system: str, skill: str, tools: list[Tool], task: str,
              client=None, effort: str = "medium") -> Trace:
    client = client or _client()
    by_name = {t.name: t for t in tools}
    # Stable prefix first (system + skill + tools) so it is cached across tasks.
    system_blocks = [{"type": "text", "text": f"{system}\n\n<skill>\n{skill}\n</skill>"}]
    messages = [{"role": "user", "content": task}]
    trace = Trace(agent=agent)
    for _ in range(MAX_TURNS):
        resp = client.beta.messages.create(
            model=MODEL, max_tokens=16000, system=system_blocks,
            tools=[t.spec() for t in tools], messages=messages,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": effort}, cache_control={"type": "ephemeral"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        u = resp.usage
        trace.model = resp.model
        trace.usage["input_tokens"] += u.input_tokens or 0
        trace.usage["output_tokens"] += u.output_tokens or 0
        trace.usage["cache_read_input_tokens"] += getattr(u, "cache_read_input_tokens", 0) or 0
        trace.thinking += [b.thinking for b in resp.content if b.type == "thinking" and b.thinking]
        if resp.stop_reason == "refusal":
            trace.error = "model declined the task"
            return trace
        # Append the full content unchanged: thinking blocks must be replayed as-is.
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if b.type == "tool_use"]
        trace.text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
        if resp.stop_reason != "tool_use" or not uses:
            if trace.decision is None:
                trace.error = "agent stopped without a decision"
            return trace
        results, decided = [], False
        for block in uses:
            tool = by_name.get(block.name)
            try:
                out = tool.fn(**block.input) if tool else {"error": f"unknown tool {block.name}"}
            except TypeError as exc:
                out = {"error": f"bad arguments: {exc}"}
            refused = isinstance(out, dict) and "error" in out
            text = json.dumps(out, default=str)
            if len(text) > RESULT_LIMIT:
                text = text[:RESULT_LIMIT] + ' ... "[truncated]"'
            trace.steps.append({"tool": block.name, "input": block.input,
                                "result": json.loads(text) if len(text) < 2000 else text[:2000],
                                "refused": refused, "terminal": bool(tool and tool.terminal)})
            if tool and tool.terminal and not refused and not decided:
                trace.decision = {"tool": block.name, "input": block.input, "result": out}
                decided = True
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": text,
                            "is_error": refused})
        messages.append({"role": "user", "content": results})
        if decided:
            return trace
    trace.error = f"no decision within {MAX_TURNS} turns"
    return trace
