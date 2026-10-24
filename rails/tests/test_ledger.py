import json
from pathlib import Path

from contracts.models import InvoiceStatus, LedgerLine
from rails.ledger import build_ledger
from world import generate

FIXTURE = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "sample"


def test_scenario_3_ledger_matches_fixture():
    w = generate(n_clean=0)
    got = build_ledger(w.events())
    assert got == [LedgerLine(**r) for r in json.loads((FIXTURE / "ledger.json").read_text())]


def test_statement_claim_alone_never_counts_as_paid():
    (line,) = build_ledger(generate(n_clean=0).events())
    assert line.paid_amount == 0 and line.status == InvoiceStatus.funded


def test_clean_world_ledger_matches_planted_ledger():
    w = generate()
    assert build_ledger(w.events()) == sorted(w.ledger, key=lambda l: l.invoice_id)


def test_due_date_needs_confirmed_terms():
    w = generate(n_clean=3)
    no_terms = [e for e in w.events() if e.type != "terms_confirmed"]
    assert all(l.due_date is None for l in build_ledger(no_terms))
    assert all(l.due_date for l in build_ledger(w.events()))


def test_unfunded_and_partial_status():
    w = generate(n_clean=2)
    evs = w.events()
    no_funding = [e for e in evs if e.payload.get("kind") != "funding"]
    assert {l.status for l in build_ledger(no_funding)} <= {InvoiceStatus.unfunded, InvoiceStatus.paid}
