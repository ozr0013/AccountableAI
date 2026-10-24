"""Every planted scenario, checked against what rails derives from the documents alone."""

from datetime import date

import pytest

from rails.aging import pattern
from rails.docs import documents_from_events
from rails.guards import accounting_check
from rails.ledger import build_ledger
from rails.matcher import gather_evidence
from rails.recon import breakdown, reconcile
from rails.trust import decide
from world import generate
from world.generator import ALL_SCENARIOS

AS_OF = date(2026, 4, 25)


@pytest.fixture(scope="module")
def full():
    w = generate(scenarios=ALL_SCENARIOS)
    return w, w.events()


def truth(w, n):
    return next(t for t in w.truth if t["scenario"] == n)


def lines(evs):
    return {l.invoice_id: l for l in build_ledger(evs)}


def test_all_nine_planted_and_ledger_matches_plan(full):
    w, evs = full
    assert sorted(t["scenario"] for t in w.truth) == list(range(1, 10))
    assert build_ledger(evs) == sorted(w.ledger, key=lambda l: l.invoice_id)
    ids = [d.id for d in w.documents]
    assert len(ids) == len(set(ids))
    for t in w.truth:
        for doc_id in t.get("must_cite", []):
            assert doc_id in ids, (t["scenario"], doc_id)


def test_deterministic(full):
    w, _ = full
    assert generate(scenarios=ALL_SCENARIOS).documents == w.documents


def test_s1_hold_is_not_owed(full):
    w, evs = full
    r = {x.invoice_id: x for x in breakdown(evs, "sunbelt")}["INV-SB-0101"]
    assert (r.held, r.owed) == (20000, 0)


def test_s2_s9_s3_unpaid_and_s9_reply_is_not_proof(full):
    w, evs = full
    ev = gather_evidence(documents_from_events(evs)).evidence
    assert decide(ev["INV-KW-0301"]).claim == "unpaid"
    d9 = decide(ev["INV-PT-0901"])
    assert d9.claim == "unpaid" and d9.conflict
    assert {"doc-reply-pt-0901", "doc-bank-peachtree-2026-03-ops"} <= {c.source_id for c in d9.winners + d9.losers}
    assert lines(evs)["INV-PT-0201"].paid_amount == 0


def test_s4_only_sunbelt_has_a_recurring_pattern(full):
    w, evs = full
    t = truth(w, 4)
    p = pattern(evs, "sunbelt", AS_OF)
    assert p.recurring and p.months == 6
    assert [h["month"] for h in p.history if h["late"]] == t["late_months"]
    for m in t["on_time_managers"]:
        assert not pattern(evs, m, AS_OF).recurring


def test_s5_paid_after_due_on_invoice_date(full):
    w, evs = full
    l = lines(evs)["INV-KW-0402"]
    assert l.paid_on > l.due_date


def test_s6_unfunded_is_owner_side(full):
    w, evs = full
    assert reconcile(evs, "peachtree")["unfunded"] == 8400.0
    assert lines(evs)["INV-PT-0601"].status == "unfunded"


def test_s7_bank_change_notice_present_and_invoice_open(full):
    w, evs = full
    docs = documents_from_events(evs)
    assert any(d.data.get("status") == "bank_change_request" for d in docs)
    assert lines(evs)["INV-SB-0701"].paid_amount == 0


def test_s8_duplicate_flagged_and_surfaces_in_recon(full):
    w, evs = full
    t = truth(w, 8)
    assert lines(evs)[t["invoice_id"]].duplicate_of == t["duplicate_of"]
    assert reconcile(evs, "sunbelt")["duplicate_funded"] == t["duplicate_funded"]


def test_books_agree_apart_from_flagged_duplicates(full):
    _, evs = full
    assert accounting_check(evs) == []
