"""Task 4: delay-cause agent. Scenarios 1, 5, 6 must never produce an alert on the manager."""


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
    ("INV-KW-0101", "keyway", Cause.authorized_hold),      # scenario 1
    ("INV-KW-0201", "keyway", Cause.paperwork_pending),  # scenario 2
    ("INV-KW-0301", "keyway", Cause.unexplained),           # scenario 3
    ("INV-KW-0601", "keyway", Cause.owner_side),         # scenario 6
    ("INV-KW-0901", "keyway", Cause.unexplained),        # scenario 9
])
def test_rules_cause_per_scenario(inv, mgr, cause):
    f = CauseAgent(None).classify_cause(*case(inv, mgr))
    assert f.cause == cause
    assert f.citations and 0 < f.confidence <= 1


def test_scenario_1_cites_agreement_and_funding():
    f = CauseAgent(None).classify_cause(*case("INV-KW-0101", "keyway"))
    assert {"doc-agreement-keyway", "doc-funding-kw-0101"} <= {c.source_id for c in f.citations}


def test_scenario_6_cites_approval_limit():
    f = CauseAgent(None).classify_cause(*case("INV-KW-0601", "keyway"))
    [limit] = [c for c in f.citations if c.kind == "agreement"]
    assert limit.locator == "8.1" and "approval limit" in f.explanation


def test_scenario_5_paid_within_the_window_is_vendor_side():
    f = CauseAgent(None).classify_cause(*case("INV-KW-0501", "keyway"))   # paid Apr 8, due Apr 11
    assert f.cause == Cause.vendor_side and "2026-04-11" in f.explanation


def test_moment_item_is_unexplained_on_the_recorded_receipt_date():
    f = CauseAgent(None).classify_cause(*case("INV-KW-0402", "keyway"))   # due Apr 7, unpaid
    assert f.cause == Cause.unexplained


def _answer(cause, cites=("doc-funding-kw-0201",), why="Held for a missing W-9."):
    return CauseAnswer(cause=cause, confidence=0.8, explanation=why,
                       citations=[{"source_id": s} for s in cites])


def test_llm_judgment_used_when_it_fits_the_facts():
    llm = ScriptedLLM(_answer(Cause.paperwork_pending, ("doc-stmt-kw-2026-04-hold",)))
    f = CauseAgent(llm).classify_cause(*case("INV-KW-0201", "keyway"))
    assert f.cause == Cause.paperwork_pending and f.explanation == "Held for a missing W-9."
    assert "hard_facts" in llm.prompts[0]


def test_llm_cannot_blame_manager_for_unfunded_invoice():
    bad = _answer(Cause.unexplained, ("doc-inv-kw-0601",), "Manager has not paid.")
    llm = ScriptedLLM(bad, bad)
    f = CauseAgent(llm).classify_cause(*case("INV-KW-0601", "keyway"))
    assert f.cause == Cause.owner_side                       # rules fallback
    assert "must be owner_side" in llm.prompts[1]


def test_llm_cannot_call_an_allowed_holdback_unexplained():
    """Scenario 1: $1,800 of $2,000 paid, 10% held until inspection. A live model once called
    this 'unexplained'; the guard now rejects that and the retry gets the problem spelled out."""
    cites = ("doc-agreement-keyway", "doc-funding-kw-0101")
    bad = _answer(Cause.unexplained, cites, "Funded and not fully paid.")
    good = _answer(Cause.authorized_hold, cites, "Within the 10% holdback until inspection.")
    llm = ScriptedLLM(bad, good)
    f = CauseAgent(llm).classify_cause(*case("INV-KW-0101", "keyway"))
    assert f.cause == Cause.authorized_hold and f.explanation.startswith("Within the 10%")
    assert "authorized hold, not unexplained" in llm.prompts[1]


def test_llm_stuck_on_unexplained_for_an_allowed_holdback_falls_back_to_the_rules():
    cites = ("doc-agreement-keyway", "doc-funding-kw-0101")
    bad = _answer(Cause.unexplained, cites, "Funded and not fully paid.")
    f = CauseAgent(ScriptedLLM(bad, bad)).classify_cause(*case("INV-KW-0101", "keyway"))
    assert f.cause == Cause.authorized_hold                   # rules fallback, never "unexplained"


def test_llm_cannot_call_a_too_large_gap_a_holdback():
    llm = ScriptedLLM(_answer(Cause.authorized_hold, ("doc-funding-kw-0305",)),
                      _answer(Cause.unexplained, ("doc-funding-kw-0305",), "Funded, unpaid."))
    f = CauseAgent(llm).classify_cause(*case("INV-KW-0301", "keyway"))
    assert f.cause == Cause.unexplained and f.explanation == "Funded, unpaid."


def test_llm_unknown_citation_rejected():
    llm = ScriptedLLM(_answer(Cause.unexplained, ("doc-nope",)), None)
    f = CauseAgent(llm).classify_cause(*case("INV-KW-0301", "keyway"))
    assert f.cause == Cause.unexplained and "doc-nope" not in {c.source_id for c in f.citations}


def test_llm_cannot_call_a_paperwork_hold_unexplained():
    """Scenario 2: the manager's statement says the payment is on hold for a missing W-9. A cheap
    model once called this 'unexplained' and the headline hold came out wrong."""
    cites = ("doc-funding-kw-0201",)
    bad = _answer(Cause.unexplained, cites, "Funded and still unpaid.")
    good = _answer(Cause.paperwork_pending, cites, "On hold for a missing W-9.")
    llm = ScriptedLLM(bad, good)
    f = CauseAgent(llm).classify_cause(*case("INV-KW-0201", "keyway"))
    assert f.cause == Cause.paperwork_pending
    assert "paperwork pending, not unexplained" in llm.prompts[1]
    f = CauseAgent(ScriptedLLM(bad, bad)).classify_cause(*case("INV-KW-0201", "keyway"))
    assert f.cause == Cause.paperwork_pending                 # rules fallback


def test_claude_rewording_the_trail_cannot_hide_a_data_fact():
    """Live run found this: Claude's trail wording dropped 'inspection pending', so the holdback
    guard never fired and Claude's 'unexplained' on scenario 1 got through."""
    from agents.trail.agent import TrailDraft
    line, docs = invoice("INV-KW-0101", "keyway")
    base = TrailAgent(None).build_trail(line, docs)
    reworded = TrailDraft(entries=[
        {"source_id": e.citation.source_id, "on": e.on.isoformat() if e.on else None,
         "claim": "Topline Roofing invoice received" if e.kind == "vendor_invoice" else e.claim}
        for e in base.entries], reasoning="Bank shows $1,800 of $2,000 paid.")
    trail = TrailAgent(ScriptedLLM(reworded)).build_trail(line, docs)
    [inv] = [e for e in trail.entries if e.kind == "vendor_invoice"]
    assert "Topline Roofing invoice received" in inv.claim and "inspection pending" in inv.claim
    terms = terms_for(world_rails(), FakeAgents(), "keyway")
    claude_says = _answer(Cause.unexplained, ("doc-funding-kw-0101",), "10% still unpaid.")
    f = CauseAgent(ScriptedLLM(claude_says, claude_says)).classify_cause(line, trail, terms)
    assert f.cause == Cause.authorized_hold                  # guard fired, rules decided


def test_claude_can_explain_a_proven_late_payment_without_fallback():
    from datetime import date

    from contracts.models import Document

    line, docs = invoice("INV-KW-0301", "keyway")
    line = line.model_copy(update={"paid_amount": 1240, "paid_on": date(2026, 4, 22)})
    bank = Document(id="late-bank-proof", kind="manager_bank", manager_id="keyway",
                    dated=date(2026, 4, 22), data={"payments": [{
                        "invoice_id": line.invoice_id, "vendor_id": line.vendor_id,
                        "amount": 1240, "on": "2026-04-22"}]})
    trail = TrailAgent(None).build_trail(line, docs + [bank])
    terms = terms_for(world_rails(), FakeAgents(), "keyway")
    explanation = "Bank proof confirms full payment on Apr 22, after the March 13 deadline."
    llm = ScriptedLLM(_answer(Cause.unexplained, (bank.id, "doc-agreement-keyway"), explanation))
    result = CauseAgent(llm).classify_cause(line, trail, terms)
    assert result.explanation == explanation
    assert len(llm.prompts) == 1
