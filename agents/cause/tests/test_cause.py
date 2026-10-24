"""Task 4: delay-cause agent. Scenarios 1, 5, 6 must never produce an alert on the manager."""

from datetime import date

import pytest

from agents.cause import CauseAgent
from agents.cause.agent import CauseAnswer
from agents.testing import ScriptedLLM, invoice, world_rails
from agents.trail import TrailAgent
from contracts.fakes import FakeAgents
from contracts.models import Cause
from workflow.terms import terms_for


def case(invoice_id, manager_id):
    line, docs = invoice(invoice_id, manager_id)
    trail = TrailAgent(None).build_trail(line, docs)
    terms = terms_for(world_rails(), FakeAgents(), manager_id)
    return line, trail, terms


@pytest.mark.parametrize("inv,mgr,cause", [
    ("INV-SB-0101", "sunbelt", Cause.authorized_hold),      # scenario 1
    ("INV-PT-0201", "peachtree", Cause.paperwork_pending),  # scenario 2
    ("INV-KW-0301", "keyway", Cause.unexplained),           # scenario 3
    ("INV-PT-0601", "peachtree", Cause.owner_side),         # scenario 6
    ("INV-PT-0901", "peachtree", Cause.unexplained),        # scenario 9
])
def test_rules_cause_per_scenario(inv, mgr, cause):
    f = CauseAgent(None).classify_cause(*case(inv, mgr))
    assert f.cause == cause
    assert f.citations and 0 < f.confidence <= 1


def test_scenario_1_cites_agreement_and_funding():
    f = CauseAgent(None).classify_cause(*case("INV-SB-0101", "sunbelt"))
    assert {"doc-agreement-sunbelt", "doc-funding-sb-0101"} <= {c.source_id for c in f.citations}


def test_scenario_6_cites_approval_limit():
    f = CauseAgent(None).classify_cause(*case("INV-PT-0601", "peachtree"))
    [limit] = [c for c in f.citations if c.kind == "agreement"]
    assert limit.locator == "8.1" and "approval limit" in f.explanation


def test_scenario_5_after_moment_is_vendor_side():
    line, trail, terms = case("INV-KW-0402", "keyway")
    before = CauseAgent(None).classify_cause(line, trail, terms)
    fixed = line.model_copy(update={"invoice_received": date(2026, 4, 1),
                                    "due_date": date(2026, 4, 11)})
    after = CauseAgent(None).classify_cause(fixed, trail, terms)
    assert before.cause == Cause.unexplained and after.cause == Cause.vendor_side


def _answer(cause, cites=("doc-funding-pt-0201",), why="Held for a missing W-9."):
    return CauseAnswer(cause=cause, confidence=0.8, explanation=why,
                       citations=[{"source_id": s} for s in cites])


def test_llm_judgment_used_when_it_fits_the_facts():
    llm = ScriptedLLM(_answer(Cause.paperwork_pending, ("doc-stmt-pt-2026-03-hold",)))
    f = CauseAgent(llm).classify_cause(*case("INV-PT-0201", "peachtree"))
    assert f.cause == Cause.paperwork_pending and f.explanation == "Held for a missing W-9."
    assert "hard_facts" in llm.prompts[0]


def test_llm_cannot_blame_manager_for_unfunded_invoice():
    bad = _answer(Cause.unexplained, ("doc-inv-pt-0601",), "Manager has not paid.")
    llm = ScriptedLLM(bad, bad)
    f = CauseAgent(llm).classify_cause(*case("INV-PT-0601", "peachtree"))
    assert f.cause == Cause.owner_side                       # rules fallback
    assert "must be owner_side" in llm.prompts[1]


def test_llm_cannot_call_a_too_large_gap_a_holdback():
    llm = ScriptedLLM(_answer(Cause.authorized_hold, ("doc-funding-kw-0305",)),
                      _answer(Cause.unexplained, ("doc-funding-kw-0305",), "Funded, unpaid."))
    f = CauseAgent(llm).classify_cause(*case("INV-KW-0301", "keyway"))
    assert f.cause == Cause.unexplained and f.explanation == "Funded, unpaid."


def test_llm_unknown_citation_rejected():
    llm = ScriptedLLM(_answer(Cause.unexplained, ("doc-nope",)), None)
    f = CauseAgent(llm).classify_cause(*case("INV-KW-0301", "keyway"))
    assert f.cause == Cause.unexplained and "doc-nope" not in {c.source_id for c in f.citations}
