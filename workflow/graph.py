"""The deterministic workflow as a LangGraph graph. Code picks every edge; no LLM routing.

    route ─┬─ assess ──▶ assess ─▶ draft_alerts ─▶ verify ─▶ chase ─▶ finish
           ├─ scan   ──▶ assess (all of one manager's invoices)
           ├─ reply  ──▶ read_reply ─▶ assess
           ├─ funding_review ─▶ funding_review (case agent -> controller approval)
           ├─ agreement ─▶ extract_terms ─▶ confirm_terms (pauses for a controller) ─▶ assess
           ├─ bank_change ─▶ bank_change (hold + identity check + controller approval)
           └─ ignore ──▶ finish
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph

from .state import Route, WorkflowState
from .steps import Steps

_AFTER_ROUTE = {
    Route.assess: "assess", Route.scan: "assess", Route.reply: "read_reply",
    Route.agreement: "extract_terms", Route.bank_change: "bank_change", Route.funding_review: "funding_review",
    Route.ignore: "finish",
}


# Our own state types, allowed through LangGraph's checkpoint (de)serializer.
SERDE_MODULES = [("contracts.models", n) for n in (
    "Event", "SourceKind", "Citation", "AgreementTerms", "Term", "AlertDraft", "AlertKind",
    "Claim", "CauseFinding", "Cause", "PaymentTrail", "TrailEntry", "Document")] + [
    ("workflow.state", "Route"), ("workflow.state", "Assessment")]


def serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=SERDE_MODULES)


def build_graph(steps: Steps, checkpointer: BaseCheckpointSaver | None = None):
    g = StateGraph(WorkflowState)
    for name in ("route", "assess", "draft_alerts", "verify", "chase", "read_reply",
                 "funding_review", "extract_terms", "confirm_terms", "bank_change", "pending",
                 "payment_review", "finish"):
        g.add_node(name, getattr(steps, name))
    g.add_edge(START, "route")
    g.add_conditional_edges("route", lambda s: _AFTER_ROUTE[s.route],
                            sorted(set(_AFTER_ROUTE.values())))
    g.add_edge("read_reply", "assess")
    g.add_edge("extract_terms", "confirm_terms")
    g.add_conditional_edges("confirm_terms", lambda s: "assess" if s.invoice_ids else "finish",
                            ["assess", "finish"])
    g.add_edge("assess", "payment_review")
    g.add_edge("payment_review", "draft_alerts")
    g.add_edge("draft_alerts", "verify")
    g.add_edge("verify", "chase")
    g.add_edge("chase", "finish")
    g.add_edge("pending", "finish")
    g.add_edge("funding_review", "finish")
    g.add_edge("bank_change", "finish")
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver(serde=serde()))
