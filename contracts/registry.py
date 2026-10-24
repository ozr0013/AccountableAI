"""Pick real or fake implementations with env vars: RAILS, AGENTS, VERIFIER, APPROVALS, BRIEFING.

Real modules must expose a no-arg factory named `build()` returning the Protocol:
    rails/__init__.py              -> build() -> Rails
    agents/__init__.py             -> build() -> Agents
    workflow/checks/wording.py     -> build() -> Verifier (code checks)
    workflow/checks/briefing.py    -> build() -> Briefer (code briefing)
    workflow/approvals.py          -> build() -> Approvals
"""

from __future__ import annotations

import importlib
import os

from .steps import Agents, Approvals, Briefer, Rails, Verifier

_REAL = {
    "RAILS": "rails",
    "AGENTS": "agents",
    "VERIFIER": "workflow.checks.wording",
    "APPROVALS": "workflow.approvals",
    "BRIEFING": "workflow.checks.briefing",
}


def _load(var: str, fake_factory):
    if os.getenv(var, "fake").lower() == "real":
        return importlib.import_module(_REAL[var]).build()
    return fake_factory()


def rails() -> Rails:
    from .fakes import FakeRails
    return _load("RAILS", FakeRails)


def agents() -> Agents:
    from .fakes import FakeAgents
    return _load("AGENTS", FakeAgents)


def verifier() -> Verifier:
    from .fakes import FakeVerifier
    return _load("VERIFIER", FakeVerifier)


def approvals() -> Approvals:
    from .fakes import FakeApprovals
    return _load("APPROVALS", FakeApprovals)


def briefer() -> Briefer:
    from .fakes import FakeBriefer
    return _load("BRIEFING", FakeBriefer)
