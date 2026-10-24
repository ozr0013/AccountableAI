"""Task 4: delay cause. Scenarios 1, 5, 6 must never produce an alert on the manager."""


import pytest

from agents.cause import classify
from agents.testing import invoice, world_rails
from agents.trail import build_trail
from contracts.fakes import FakeAgents
from contracts.models import Cause
from workflow.terms import terms_for


def case(invoice_id, manager_id):
    line, docs = invoice(invoice_id, manager_id)
    trail = build_trail(line, docs)
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
    f = classify(*case(inv, mgr))
    assert f.cause == cause
    assert f.citations and 0 < f.confidence <= 1


def test_scenario_1_cites_agreement_and_funding():
    f = classify(*case("INV-KW-0101", "keyway"))
    assert {"doc-agreement-keyway", "doc-funding-kw-0101"} <= {c.source_id for c in f.citations}


def test_scenario_6_cites_approval_limit():
    f = classify(*case("INV-KW-0601", "keyway"))
    [limit] = [c for c in f.citations if c.kind == "agreement"]
    assert limit.locator == "8.1" and "approval limit" in f.explanation


def test_scenario_5_paid_within_the_window_is_vendor_side():
    f = classify(*case("INV-KW-0501", "keyway"))   # paid Apr 8, due Apr 11
    assert f.cause == Cause.vendor_side and "2026-04-11" in f.explanation


def test_moment_item_is_unexplained_on_the_recorded_receipt_date():
    f = classify(*case("INV-KW-0402", "keyway"))   # due Apr 7, unpaid
    assert f.cause == Cause.unexplained


def test_paperwork_hold_ends_when_the_vendor_supplies_it():
    """Scenario 2 after the W-9 arrives: the statement's hold no longer explains the unpaid money."""
    from datetime import date

    from contracts.models import Document, SourceKind
    line, docs = invoice("INV-KW-0201", "keyway")
    held = max(d.dated for d in docs if d.kind == SourceKind.manager_statement)
    w9 = Document(id="doc-w9-0201", kind=SourceKind.vendor_notice, manager_id="keyway",
                  vendor_id=line.vendor_id, dated=max(held, date(2026, 4, 20)), text="W-9 provided.",
                  data={"invoice_id": "INV-KW-0201", "status": "paperwork_received"})
    terms = terms_for(world_rails(), FakeAgents(), "keyway")
    before = classify(line, build_trail(line, docs), terms)
    after = classify(line, build_trail(line, docs + [w9]), terms)
    assert before.cause == Cause.paperwork_pending
    assert after.cause == Cause.unexplained
