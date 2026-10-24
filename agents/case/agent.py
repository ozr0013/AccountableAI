"""Case agent (task 5b): evidence pack and proposed hold for the controller.

Code fixes the hold amount (sum still owed on open unexplained items) and the evidence list.
The LLM writes the controller-facing summary from the alerts; guards require it to state the
exact hold amount, cite only evidence in the pack, and stay neutral. It only recommends:
proposed_by is always "case_agent" and only a controller can approve.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from contracts.models import Alert, Case

from ..guards import wording_problems
from ..llm import StructuredLLM, guarded_call
from . import rules
from .prompts import SYSTEM


class CaseSummary(BaseModel):
    summary: str = Field(description="3-4 sentences for the controller, with the hold amount")
    cited_source_ids: list[str] = Field(description="evidence ids the summary relies on")


class CaseAgent:
    def __init__(self, llm: StructuredLLM | None = None):
        self.llm = llm

    def build_case(self, manager_id: str, alerts: list[Alert]) -> Case:
        base = rules.build_case(manager_id, alerts)
        if not base.alerts:
            return base
        allowed = {c.source_id for c in base.evidence}
        amount = rules.money(base.proposed_hold)

        def check(out: CaseSummary) -> list[str]:
            p = [] if amount in out.summary else [f"the summary must state the hold amount {amount}"]
            p += [f"cites {s!r}, which is not in the evidence pack"
                  for s in out.cited_source_ids if s not in allowed]
            if any(w in out.summary.lower() for w in ("we held", "has been held", "we froze")):
                p.append("agents only recommend a hold; never say one was placed")
            return p + wording_problems(out.summary)

        pack = {
            "manager_id": manager_id, "proposed_hold": base.proposed_hold,
            "alerts": [{"id": a.draft.id, "headline": a.draft.headline,
                        "claims": [{"text": c.text, "cites": [x.source_id for x in c.citations]}
                                   for c in a.draft.claims]}
                       for a in alerts if a.draft.id in base.alerts],
        }
        out, _ = guarded_call(self.llm, CaseSummary, SYSTEM,
                              f"<case>\n{json.dumps(pack, indent=1)}\n</case>", check)
        return base if out is None else base.model_copy(update={"summary": out.summary})
