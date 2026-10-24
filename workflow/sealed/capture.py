"""What the blind run records about the agents. Uses only the system's own data (ledger, trace),
never the answers: causes are captured for every invoice, not for the planted ones."""

from __future__ import annotations

import os
import re

from workflow.terms import terms_for

_CAUSE_LINE = re.compile(r"^(INV-[\w-]+): (\w+)(?:, cause (\w+))?")


def agents():
    from agents import RealAgents
    if os.getenv("LIVE") == "1" and os.getenv("ANTHROPIC_API_KEY"):
        from agents.llm import default_llm
        return RealAgents(default_llm())
    return RealAgents(None)


class CapturingAgents:
    """Keeps the latest CauseFinding per invoice; everything else passes through."""

    def __init__(self, inner):
        self.inner, self.findings = inner, {}

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def classify_cause(self, line, trail, terms):
        f = self.inner.classify_cause(line, trail, terms)
        self.findings[line.invoice_id] = f
        return f


def statuses(results) -> dict[str, str]:
    """Latest status per invoice from the workflow's trace (paid / not_due / delayed / ...)."""
    out: dict[str, str] = {}
    for r in results:
        for line in r.trace:
            if m := _CAUSE_LINE.match(line):
                out[m.group(1)] = m.group(2)
    return out


def explain_all(rails, caught: CapturingAgents, status: dict[str, str]) -> None:
    """Ask the cause agent about every invoice that ended paid without a finding, so cause
    accuracy can be graded on any item. Covers the whole ledger: the run doesn't know which
    invoices are planted scenarios."""
    managers = sorted({e.manager_id for e in rails.events() if e.manager_id})
    for m in managers:
        terms = terms_for(rails, caught, m)
        if terms is None:
            continue
        for line in rails.ledger(m):
            iid = line.invoice_id
            if iid in caught.findings and status.get(iid) == "delayed":
                continue
            if status.get(iid, "paid") == "paid":
                trail = caught.build_trail(line, rails.documents(m, iid))
                caught.classify_cause(line, trail, terms)
