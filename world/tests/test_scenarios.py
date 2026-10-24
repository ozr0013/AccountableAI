"""Every planted scenario, checked against what rails derives from the documents alone."""

from datetime import date, datetime, time, timezone

import pytest

from rails.aging import pattern
from rails.docs import documents_from_events
from rails.guards import accounting_check
from rails.ledger import build_ledger
from rails.matcher import gather_evidence
from rails.recon import breakdown, reconcile
from rails.trust import decide
from world import generate
from world.generator import ALL_SCENARIOS, LATE_MONTHS, MOMENT, PAY_DAYS, WORLD_TODAY

TODAY = WORLD_TODAY


@pytest.fixture(scope="module")
def full():
    w = generate(scenarios=ALL_SCENARIOS)
    return w, w.events()


def truth(w, n):
    return next(t for t in w.truth if t["scenario"] == n)


def lines(evs):
    return {l.invoice_id: l for l in build_ledger(evs)}


def test_one_middleman_twenty_vendors(full):
    w, evs = full
    assert [m.id for m in w.managers] == ["keyway"]
    assert {e.manager_id for e in evs} <= {"keyway", None}
    assert 18 <= len(w.vendors) <= 22
    assert all(l.manager_id == "keyway" for l in w.ledger)


def test_nothing_is_dated_after_world_today(full):
    w, evs = full
    end = datetime.combine(WORLD_TODAY, time.max, tzinfo=timezone.utc)
    late = [e.id for e in evs if e.valid_from > end or e.learned_at > end]
    assert late == []
    assert all(d.dated is None or d.dated <= WORLD_TODAY for d in w.documents)
    assert w.as_of == WORLD_TODAY


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
    r = {x.invoice_id: x for x in breakdown(evs, "keyway")}["INV-KW-0101"]
    assert (r.held, r.owed) == (20000, 0)
    l = lines(evs)["INV-KW-0101"]
    assert l.paid_on <= l.due_date                       # the 90% went out inside the window


def test_s2_s3_s9_unpaid_and_s9_reply_is_not_proof(full):
    w, evs = full
    ev = gather_evidence(documents_from_events(evs)).evidence
    assert decide(ev["INV-KW-0301"]).claim == "unpaid"
    d9 = decide(ev["INV-KW-0901"])
    assert d9.claim == "unpaid" and d9.conflict
    assert {"doc-reply-kw-0901", "doc-bank-kw-2026-04-0901"} <= {c.source_id for c in d9.winners + d9.losers}
    l2 = lines(evs)["INV-KW-0201"]
    assert l2.paid_amount == 0 and l2.due_date < TODAY          # past due, so it gets assessed


def test_s4_pattern_is_measured_against_the_agreement(full):
    w, evs = full
    t = truth(w, 4)
    p = pattern(evs, "keyway", TODAY)
    assert (p.months, p.late, p.recurring, p.payment_window_days) == (4, 3, True, PAY_DAYS)
    assert [h["month"] for h in p.history] == t["months"]
    assert [h["month"] for h in p.history if h["late"]] == t["late_months"] == sorted(LATE_MONTHS["keyway"])
    for h in p.history:
        assert all(d > 0 for d in h["days_past_window"].values())
        assert h["max_days_past_window"] == max(h["days_past_window"].values(), default=0)


def test_s4_late_months_hold_without_the_unexplained_items(full):
    """Jan to Mar are late because of invoices paid past the window, not only the open items."""
    w, evs = full
    open_items = {"INV-KW-0201", "INV-KW-0301", "INV-KW-0402", "INV-KW-0901"}
    p = pattern(evs, "keyway", TODAY)
    for h in p.history:
        if h["late"]:
            assert set(h["invoice_ids"]) - open_items, h["month"]


def test_without_s4_keyway_is_not_a_pattern():
    evs = generate(scenarios=(3,)).events()
    assert not pattern(evs, "keyway", TODAY).recurring


def test_s5_is_a_different_electrician_paid_on_time(full):
    w, evs = full
    l = lines(evs)["INV-KW-0501"]
    assert l.vendor_id != w.moment["vendor_id"] and l.invoice_id != w.moment["invoice_id"]
    assert (l.invoice_received, l.paid_on, l.due_date) == (date(2026, 4, 1), date(2026, 4, 8),
                                                           date(2026, 4, 11))


def test_s6_unfunded_is_owner_side(full):
    w, evs = full
    assert reconcile(evs, "keyway")["unfunded"] == 8400.0
    assert lines(evs)["INV-KW-0601"].status == "unfunded"


def test_s7_bank_change_notice_present_and_invoice_not_due(full):
    w, evs = full
    docs = documents_from_events(evs)
    assert any(d.data.get("status") == "bank_change_request" for d in docs)
    l = lines(evs)["INV-KW-0701"]
    assert l.paid_amount == 0 and l.due_date > TODAY


def test_s8_duplicate_flagged_and_surfaces_in_recon(full):
    w, evs = full
    t = truth(w, 8)
    assert lines(evs)[t["invoice_id"]].duplicate_of == t["duplicate_of"]
    assert reconcile(evs, "keyway")["duplicate_funded"] == t["duplicate_funded"]


def test_moment_three_unexplained_items_total_3140(full):
    w, evs = full
    m = w.moment
    ls = lines(evs)
    overdue = [i for i in m["unexplained_before"]
               if ls[i].paid_amount == 0 and ls[i].funded_amount and ls[i].due_date < TODAY]
    assert overdue == m["unexplained_before"]
    assert sum(ls[i].amount for i in overdue) == m["unexplained_total_before"] == 3140.0
    e = ls[m["invoice_id"]]
    assert (e.invoice_received, e.due_date, e.funded_on.month) == (date(2026, 3, 28), date(2026, 4, 7), 3)
    assert MOMENT["value"] == m["corrected"]["received"] == "2026-04-15"


def test_recon_ties_out_for_keyway(full):
    """funded = paid + authorized holds + still owed; the only gap is the flagged duplicate."""
    _, evs = full
    r = reconcile(evs, "keyway")
    assert round(r["paid"] + r["held"] + r["owed"] + r["duplicate_funded"], 2) == r["funded"]
    assert r["diff"] == r["duplicate_funded"] == 320.0
    # still owed = the three unexplained items + paperwork-pending + bank-change item (not yet due)
    assert r["owed"] == 3140.0 + 650.0 + 4800.0
    assert r["held"] == 200.0


def test_books_agree_apart_from_flagged_duplicates(full):
    _, evs = full
    assert accounting_check(evs) == []


def test_scenario_spec_file_is_current():
    from world.spec import SPEC_PATH, render
    assert SPEC_PATH.read_text() == render(), "run: python -m world.spec"


def test_scenario_spec_lists_every_item():
    import json
    from world.spec import SPEC_PATH
    spec = json.loads(SPEC_PATH.read_text())
    assert spec["demo_date"] == "2026-04-22" and spec["middleman"]["id"] == "keyway"
    assert [i["scenario"] for i in spec["items"]] == list(range(1, 10))
    m = spec["moment"]
    assert (m["item_id"], m["amount"], m["unexplained_total_before"], m["unexplained_total_after"]) == \
           ("INV-KW-0402", 900.0, 3140.0, 2240.0)
