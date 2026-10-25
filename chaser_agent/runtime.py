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
import queue
import threading
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

MODEL = os.getenv("PIPELINE_MODEL", "claude-opus-5-5")
MAX_TURNS = 10
RESULT_LIMIT = 8000
SKILLS = Path(__file__).resolve().parent / "skills"


# -- live events: every subscriber (the dashboard's SSE stream) gets each event as it happens ----
_SUBSCRIBERS: set[queue.Queue] = set()
_SUB_LOCK = threading.Lock()
CONTEXT: ContextVar[dict] = ContextVar("pipeline_context", default={})


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=5000)
    with _SUB_LOCK:
        _SUBSCRIBERS.add(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _SUB_LOCK:
        _SUBSCRIBERS.discard(q)


def listening() -> bool:
    return bool(_SUBSCRIBERS)


def emit(event: dict) -> None:
    """Publish one event, tagged with the current task (agent, invoice, day)."""
    if not _SUBSCRIBERS:
        return
    payload = {**CONTEXT.get(), **event}
    with _SUB_LOCK:
        for q in list(_SUBSCRIBERS):
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass


def _call(client, **kw):
    """One model turn. Streamed when someone is watching, so reasoning shows as it is written."""
    stream_fn = getattr(client.beta.messages, "stream", None)
    if not listening() or stream_fn is None:
        return client.beta.messages.create(**kw)
    with stream_fn(**kw) as stream:
        for ev in stream:
            if ev.type == "content_block_start" and ev.content_block.type == "tool_use":
                emit({"type": "tool_call", "tool": ev.content_block.name})
            elif ev.type == "content_block_delta":
                d = ev.delta
                if d.type == "thinking_delta" and d.thinking:
                    emit({"type": "thinking", "text": d.thinking})
                elif d.type == "text_delta" and d.text:
                    emit({"type": "text", "text": d.text})
        return stream.get_final_message()


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
              client=None, effort: str = "medium", max_turns: int = MAX_TURNS) -> Trace:
    client = client or _client()
    by_name = {t.name: t for t in tools}
    # Stable prefix first (system + skill + tools) so it is cached across tasks.
    system_blocks = [{"type": "text", "text": f"{system}\n\n<skill>\n{skill}\n</skill>"}]
    messages = [{"role": "user", "content": task}]
    trace = Trace(agent=agent)
    emit({"type": "agent_started"})
    for _ in range(max_turns):
        resp = _call(
            client, model=MODEL, max_tokens=16000, system=system_blocks,
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
        emit({"type": "usage", "input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
              "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0})
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
            emit({"type": "tool_result", "tool": block.name, "input": block.input, "refused": refused,
                  "terminal": bool(tool and tool.terminal), "preview": text[:600]})
            trace.steps.append({"tool": block.name, "input": block.input,
                                "result": json.loads(text) if len(text) < 2000 else text[:2000],
                                "refused": refused, "terminal": bool(tool and tool.terminal)})
            if tool and tool.terminal and not refused and not decided:
                trace.decision = {"tool": block.name, "input": block.input, "result": out}
                decided = True
                emit({"type": "decision", "tool": block.name, "input": block.input})
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": text,
                            "is_error": refused})
        messages.append({"role": "user", "content": results})
        if decided:
            return trace
    trace.error = f"no decision within {max_turns} turns"
    return trace
