"""The deterministic workflow as a LangGraph graph. Code picks every edge; no LLM routing.

    route ─┬─ assess ──▶ assess ─▶ draft_alerts ─▶ verify ─▶ chase ─▶ finish
           ├─ scan   ──▶ assess (all of one manager's invoices)
           ├─ reply  ──▶ read_reply ─▶ assess
           ├─ agreement | bank_change | funding_review ─▶ pending (later tasks)
           └─ ignore ──▶ finish
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .state import Route, WorkflowState
from .steps import Steps

_AFTER_ROUTE = {
    Route.assess: "assess", Route.scan: "assess", Route.reply: "read_reply",
    Route.agreement: "pending", Route.bank_change: "pending", Route.funding_review: "pending",
    Route.ignore: "finish",
}


def build_graph(steps: Steps, checkpointer: BaseCheckpointSaver | None = None):
    g = StateGraph(WorkflowState)
    for name in ("route", "assess", "draft_alerts", "verify", "chase", "read_reply",
                 "pending", "finish"):
        g.add_node(name, getattr(steps, name))
    g.add_edge(START, "route")
    g.add_conditional_edges("route", lambda s: _AFTER_ROUTE[s.route],
                            sorted(set(_AFTER_ROUTE.values())))
    g.add_edge("read_reply", "assess")
    g.add_edge("assess", "draft_alerts")
    g.add_edge("draft_alerts", "verify")
    g.add_edge("verify", "chase")
    g.add_edge("chase", "finish")
    g.add_edge("pending", "finish")
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())
