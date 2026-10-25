"""The agreement agent: a LangGraph graph that reads one document with tools and escalates gaps.

    parse ──▶ route ──▶ agent ⇄ tools ──▶ check ──▶ escalate ──▶ END
                 │        (Claude picks tool calls)      └──────▶ END (complete)
                 └──▶ rules_agent ───────────▶ check            (no model: same tools, fixed plan)
                 └──▶ unreadable ────────────▶ check            (no text layer: nothing to read)

Tools: read_header, list_sections, read_section, search, find_candidates, record_field,
mark_missing, mark_unclear. record_field is guarded: the quote must appear verbatim in the
document, otherwise the tool refuses and the agent has to look again. Nothing is invented.
Escalations are drafts for the owner; nothing is sent from here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from . import fields as F
from .models import Escalation, FieldResult, Parsed, Review, SourceDoc, norm
from .parser import parse

log = logging.getLogger(__name__)

MODEL = os.getenv("AGREEMENT_MODEL", "claude-sonnet-5-5")
PRICE = {"claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0), "claude-haiku-5-5": (0.10, 0.50)}
MAX_TURNS = 14

SYSTEM = """You review a business document for Oakline Homes, the owner. It came in by email or \
from a folder. Find these fields and record each one with the tools:

{fields}

How to work:
- Start with read_header (the top of the document usually names the parties and the date) and \
list_sections, then read the sections that matter. Use search and find_candidates to locate \
wording. Read before you record.
- record_field needs a quote copied verbatim from the document. The tool refuses quotes that are \
not in the document; if it refuses, read the section again and copy the exact words.
- If you searched and a field is not there, call mark_missing with what you searched. If it is \
there but ambiguous or contradictory (two different due dates, "TBD", a blank), call mark_unclear.
- Never guess and never fill a field from general knowledge. A missing field is a useful result: \
the owner will be told.
- query_books runs read-only SQL over Oakline's books. Use it to check the document against \
reality once the fields are recorded: the vendor's past invoice amounts against the stated rate, \
the bank details in the agreement against where Keyway's payments to that vendor actually went. \
Mention any disagreement in your final sentence; it never changes a quoted field.
- When every field has been recorded, marked missing or marked unclear, stop calling tools and \
reply with one sentence summarizing the document."""


def _books_guide() -> str:
    from chaser_agent import books
    return books.GUIDE


class State(TypedDict, total=False):
    doc: SourceDoc
    parsed: Parsed
    results: dict[str, FieldResult]
    steps: list[str]
    messages: list[Any]
    turns: int
    cost: float
    mode: str
    escalation: Escalation | None
    _tools: Any                         # Tools over this document (model path)
    _model: Any                         # Claude with the tools bound (model path)


# -- tools --------------------------------------------------------------------------------
class Tools:
    """Document tools over one parsed document. Results land in `self.results`."""

    def __init__(self, parsed: Parsed, by: str):
        self.p, self.by = parsed, by
        self.specs = {s.name: s for s in F.SPECS[parsed.kind]}
        self.results: dict[str, FieldResult] = {}
        self.steps: list[str] = []

    def _log(self, s: str) -> None:
        self.steps.append(s)

    def read_header(self) -> list[str]:
        self._log("read the header (top of the document)")
        return self.p.header

    def list_sections(self) -> list[dict]:
        self._log(f"listed {len(self.p.sections)} sections")
        return [{"number": s.number, "title": s.title, "page": s.page} for s in self.p.sections]

    def read_section(self, number: str) -> str:
        sec = next((s for s in self.p.sections if s.number == number.strip(" §")), None) \
            or next((s for s in self.p.sections if number.lower() in s.title.lower()), None)
        self._log(f"read section {number}" + ("" if sec else " (not found)"))
        if sec is None:
            return f"no section {number!r}; sections: " + ", ".join(s.number or s.title for s in self.p.sections)
        return f"§{sec.number} {sec.title}\n{sec.text}"

    def search(self, query: str) -> list[dict]:
        hits = F.search(self.p, query)
        self._log(f"searched for {query!r}: {len(hits)} hit(s)")
        return hits

    def find_candidates(self, field: str) -> list[dict]:
        c = F.find(field, self.p)
        self._log(f"looked for {field}: {len(c)} candidate(s)")
        return [{"value": v, "quote": q, "where": self.p.where(q) or ""} for v, q in c[:5]]

    def record_field(self, field: str, value: str, quote: str) -> str:
        if field not in self.specs:
            return f"error: unknown field {field!r}; fields: {', '.join(self.specs)}"
        if not quote or norm(quote) not in norm(self.p.text):
            self._log(f"refused {field}: quote not in the document")
            return "error: that quote is not in the document. Copy the exact words from the document."
        # names and terms must come out of the quote itself
        if norm(str(value)) not in norm(quote) and not any(ch.isdigit() for ch in str(value)) \
                and not set(norm(str(value)).split()) <= set(norm(quote).replace(",", " ").split()):
            self._log(f"refused {field}: value not in its quote")
            return "error: the value must appear in the quote."
        spec = self.specs[field]
        self.results[field] = FieldResult(name=field, label=spec.label, status="found",
                                          value=str(value).strip(), quote=quote.strip(),
                                          location=self.p.where(quote) or "", by=self.by)
        self._log(f"recorded {spec.label}: {value}")
        return "recorded"

    def mark_missing(self, field: str, reason: str) -> str:
        return self._mark(field, "missing", reason)

    def mark_unclear(self, field: str, reason: str) -> str:
        return self._mark(field, "unclear", reason)

    def _mark(self, field: str, status: str, reason: str) -> str:
        if field not in self.specs:
            return f"error: unknown field {field!r}"
        if self.results.get(field, None) and self.results[field].status == "found":
            return "already recorded as found"
        spec = self.specs[field]
        self.results[field] = FieldResult(name=field, label=spec.label, status=status, note=reason, by=self.by)
        self._log(f"marked {spec.label} {status}: {reason}")
        return "noted"

    def query_books(self, purpose: str, sql: str) -> dict:
        """Read-only SQL over the books (AGENT_BOOKS_SCHEMA, default the live books)."""
        import os

        from chaser_agent import books
        self._log(f"queried the books: {purpose}")
        return books.tool_result(os.getenv("AGENT_BOOKS_SCHEMA", "public"), sql, purpose)

    # langchain tool schemas, for the model
    def as_langchain(self):
        from langchain_core.tools import StructuredTool
        fns = [self.read_header, self.list_sections, self.read_section, self.search,
               self.find_candidates, self.record_field, self.mark_missing, self.mark_unclear,
               self.query_books]
        docs = {
            "read_header": "The first lines of the document: title, parties, date.",
            "list_sections": "Numbered sections with titles and pages.",
            "read_section": "Full text of a section, by number (e.g. '4.2') or title word.",
            "search": "Sentences containing every word of the query.",
            "find_candidates": "Rule-based guesses for a field, with quotes. Check them before recording.",
            "record_field": "Record a field's value with a verbatim quote from the document.",
            "mark_missing": "The field is not in the document; say what you searched.",
            "mark_unclear": "The field is present but ambiguous, blank or contradictory; say why.",
            "query_books": "Investigate the books with read-only SQL (purpose, sql). " + _books_guide(),
        }
        return [StructuredTool.from_function(f, name=f.__name__, description=docs[f.__name__]) for f in fns]


# -- nodes --------------------------------------------------------------------------------
def node_parse(state: State) -> State:
    p = parse(state["doc"])
    return {"parsed": p, "steps": [(f"parsed {state['doc'].filename}: {len(p.pages)} page(s), "
                                    f"{len(p.sections)} sections, kind {p.kind} ({p.kind_reason})")]}


def route(state: State) -> str:
    if not state["parsed"].text.strip():
        return "unreadable"
    if state["parsed"].kind == "other":
        return "not_relevant"           # a resume, a form, a flyer: nothing to check, nothing to escalate
    return "agent" if state.get("mode") == "model" else "rules_agent"


def node_not_relevant(state: State) -> State:
    return {"results": {}, "steps": state["steps"] + ["not an agreement or invoice: skipped, nothing escalated"]}


def node_unreadable(state: State) -> State:
    p = state["parsed"]
    res = {s.name: FieldResult(name=s.name, label=s.label, status="unclear",
                               note="no readable text in the file (a scan without a text layer?)")
           for s in F.SPECS[p.kind]}
    return {"results": res, "steps": state["steps"] + ["no readable text: every field unclear"]}


def node_rules_agent(state: State) -> State:
    """The same tools, called in a fixed order: header, sections, then each field."""
    t = Tools(state["parsed"], by="rules")
    t.read_header()
    t.list_sections()
    for spec in F.SPECS[state["parsed"].kind]:
        cands = t.find_candidates(spec.name)
        if cands and t.record_field(spec.name, cands[0]["value"], cands[0]["quote"]) == "recorded":
            continue
        words = {"payment_due": "due", "payment_info": "payment", "payment_amount": "$",
                 "owner_name": "between", "vendor_name": "between"}.get(spec.name)
        hits = t.search(words) if words and words != "$" else []
        t.mark_missing(spec.name, f"Not stated in the document (looked for {spec.hint})."
                       + (f" {len(hits)} sentence(s) mention {words!r}, none gives it." if hits else ""))
    return {"results": t.results, "steps": state["steps"] + t.steps}


def _llm():
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model=MODEL, max_tokens=4000, max_retries=2, timeout=120)


def node_agent(state: State) -> State:
    """One model turn: Claude reads the conversation and picks the next tool calls."""
    from langchain_core.messages import HumanMessage, SystemMessage
    p = state["parsed"]
    tools = state.get("_tools")          # set by review() for the model path
    msgs = state.get("messages") or [
        SystemMessage(SYSTEM.format(fields="\n".join(f"- {s.name}: {s.label} ({s.hint})"
                                                     + ("" if s.required else " [optional]")
                                                     for s in F.SPECS[p.kind]))),
        HumanMessage(f"Document: {state['doc'].filename} ({p.kind}: {p.kind_reason}). "
                     f"{len(p.pages)} page(s). Begin."),
    ]
    resp = state["_model"].invoke(msgs)
    u = getattr(resp, "usage_metadata", None) or {}
    pin, pout = PRICE.get(MODEL, (2.0, 10.0))
    cost = (u.get("input_tokens", 0) * pin + u.get("output_tokens", 0) * pout) / 1e6
    calls = [c["name"] for c in getattr(resp, "tool_calls", [])]
    return {"messages": msgs + [resp], "turns": state.get("turns", 0) + 1,
            "cost": state.get("cost", 0.0) + cost,
            "steps": state["steps"] + [f"model turn {state.get('turns', 0) + 1}: "
                                       + (", ".join(calls) if calls else "done")],
            "_tools": tools}


def after_agent(state: State) -> str:
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None) and state.get("turns", 0) < MAX_TURNS:
        return "tools"
    return "check"


def node_tools(state: State) -> State:
    """Run the tool calls the model asked for and hand back the results."""
    from langchain_core.messages import ToolMessage
    tools: Tools = state["_tools"]
    by_name = {t.name: t for t in tools.as_langchain()}
    out = []
    n0 = len(tools.steps)
    for call in state["messages"][-1].tool_calls:
        fn = by_name.get(call["name"])
        try:
            result = fn.invoke(call["args"]) if fn else f"error: no tool {call['name']}"
        except Exception as e:                               # noqa: BLE001 - tell the model
            result = f"error: {e}"
        out.append(ToolMessage(content=json.dumps(result, default=str)[:6000], tool_call_id=call["id"]))
    return {"messages": state["messages"] + out, "results": dict(tools.results),
            "steps": state["steps"] + tools.steps[n0:]}


def node_check(state: State) -> State:
    """Every required field must be found. Whatever the agent didn't settle counts as missing."""
    p = state["parsed"]
    res = dict(state.get("results") or {})
    if state.get("_tools") is not None:
        res = {**res, **state["_tools"].results}
    for s in F.SPECS[p.kind]:
        if s.name not in res:
            res[s.name] = FieldResult(name=s.name, label=s.label, status="missing",
                                      note="the agent did not find it", by="rules")
    gaps = [r for s in F.SPECS[p.kind] if s.required and (r := res[s.name]).status != "found"]
    return {"results": res, "steps": state["steps"] + [
        f"checked {len(res)} fields: " + (f"{len(gaps)} required field(s) need the owner" if gaps else "complete")]}


def needs_owner(state: State) -> str:
    p = state["parsed"]
    req = {s.name for s in F.SPECS[p.kind] if s.required}
    return "escalate" if any(r.status != "found" for n, r in state["results"].items() if n in req) else END


def node_escalate(state: State) -> State:
    doc, p = state["doc"], state["parsed"]
    req = [s.name for s in F.SPECS[p.kind] if s.required]
    gaps = [state["results"][n] for n in req if state["results"][n].status != "found"]
    found = {n: r for n, r in state["results"].items() if r.status == "found"}
    who = (found.get("vendor_name") or found.get("owner_name"))
    party = who.value if who else (doc.sender or "an unknown sender")
    critical = {"payment_due", "payment_info", "payment_amount"}
    severity = "high" if any(g.name in critical for g in gaps) else "medium"
    what = ", ".join(g.label.lower() for g in gaps)
    lines = [f"{p.kind.title()} “{doc.filename}” from {party} can't be relied on yet. Missing or unclear: {what}.", ""]
    for g in gaps:
        lines.append(f"- {g.label}: {g.status}. {g.note}".rstrip())
    if found:
        lines += ["", "Found:"] + [f"- {r.label}: {r.value} ({r.location or 'in the document'})" for r in found.values()]
    lines += ["", "Suggested next step: ask "
              + (party if who else "the sender")
              + " for a corrected copy that states " + what + ". "
              "No payment should be scheduled from this document until then.",
              "", "(Drafted by the agreement agent. Not sent.)"]
    esc = Escalation(
        id="esc-" + hashlib.sha1(f"{doc.id}|{what}".encode()).hexdigest()[:10], doc_id=doc.id,
        subject=f"Action needed: {doc.filename} is missing {what}", body="\n".join(lines),
        items=gaps, severity=severity, created_at=datetime.now(UTC))
    return {"escalation": esc, "steps": state["steps"] + [f"escalated to the owner ({severity}): {what}"]}


def build_graph():
    g = StateGraph(State)
    g.add_node("parse", node_parse)
    g.add_node("rules_agent", node_rules_agent)
    g.add_node("agent", node_agent)
    g.add_node("tools", node_tools)
    g.add_node("unreadable", node_unreadable)
    g.add_node("not_relevant", node_not_relevant)
    g.add_node("check", node_check)
    g.add_node("escalate", node_escalate)
    g.add_edge(START, "parse")
    g.add_conditional_edges("parse", route, {"agent": "agent", "rules_agent": "rules_agent",
                                             "unreadable": "unreadable", "not_relevant": "not_relevant"})
    g.add_edge("not_relevant", END)
    g.add_conditional_edges("agent", after_agent, {"tools": "tools", "check": "check"})
    g.add_edge("tools", "agent")
    g.add_edge("rules_agent", "check")
    g.add_edge("unreadable", "check")
    g.add_conditional_edges("check", needs_owner, {"escalate": "escalate", END: END})
    g.add_edge("escalate", END)
    return g.compile()


_GRAPH = None


def review(doc: SourceDoc, use_model: bool = False, model=None) -> Review:
    """Run the agreement agent on one document. `model`: a chat model with tools bound (tests
    pass a scripted one); by default Claude is used when use_model is set."""
    global _GRAPH
    _GRAPH = _GRAPH or build_graph()
    mode = "model" if use_model else "rules"
    init: dict = {"doc": doc, "mode": mode, "steps": [], "results": {}}
    if use_model:
        p = parse(doc)
        tools = Tools(p, by="model")
        init["_tools"] = tools
        init["_model"] = model if model is not None else _llm().bind_tools(tools.as_langchain())
    out = _GRAPH.invoke(init, {"recursion_limit": 4 * MAX_TURNS + 10})
    p: Parsed = out["parsed"]
    order = [s.name for s in F.SPECS[p.kind]]
    res = out["results"]
    return Review(doc_id=doc.id, filename=doc.filename, source=doc.source, sender=doc.sender,
                  subject=doc.subject, kind=p.kind, kind_reason=p.kind_reason,
                  relevant=p.kind != "other" or not p.text.strip(),
                  fields=[res[n] for n in order if n in res],
                  complete=out.get("escalation") is None, escalation=out.get("escalation"),
                  steps=out["steps"], mode=mode, cost_usd=round(out.get("cost", 0.0), 5))
