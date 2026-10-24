"""Smoke test: scenario 3 flows end to end through the stand-ins."""

from datetime import date

from contracts import registry
from contracts.models import AlertDraft, AlertKind, Cause, Claim, VerdictStatus


def test_scenario_3_through_fakes():
    rails, agents, verifier = registry.rails(), registry.agents(), registry.verifier()

    agreement = next(d for d in rails.documents("keyway") if d.kind == "agreement")
    terms = agents.extract_terms(agreement)
    assert terms.vendor_payment_days.value == 10

    [line] = rails.unpaid("keyway", date(2026, 4, 25))
    docs = rails.documents("keyway", line.invoice_id)
    trail = agents.build_trail(line, docs)
    assert trail.conflict and trail.winning_claim == "unpaid"

    finding = agents.classify_cause(line, trail, terms)
    assert finding.cause == Cause.unexplained

    draft = AlertDraft(
        id="a1", kind=AlertKind.unexplained_unpaid, manager_id="keyway",
        invoice_ids=[line.invoice_id], amount=line.amount,
        headline="Rio Grande Plumbing unpaid $1,240",
        claims=[Claim(text=finding.explanation, citations=finding.citations)],
    )
    assert verifier.verify(draft, docs).status == VerdictStatus.approved


def test_fake_verifier_blocks_uncited_and_accusatory():
    rails, verifier = registry.rails(), registry.verifier()
    draft = AlertDraft(
        id="a2", kind=AlertKind.unexplained_unpaid, manager_id="keyway",
        invoice_ids=["INV-KW-0301"], headline="Keyway committed fraud",
        claims=[Claim(text="They kept the money", citations=[])],
    )
    v = verifier.verify(draft, rails.documents("keyway"))
    assert v.status == VerdictStatus.blocked
    assert v.uncited_claims and "fraud" in v.banned_terms


def test_reconciles():
    assert registry.rails().reconcile("keyway")["diff"] == 0
