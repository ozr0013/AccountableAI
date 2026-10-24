from datetime import date, datetime, timezone

import pytest

from contracts.models import Document, Event, SourceKind
from rails.events.ingest import document_event
from rails.recon import breakdown, reconcile
from world import generate


def events(*docs, holdback=None):
    ts = datetime(2026, 1, 2, tzinfo=timezone.utc)
    evs = [document_event(d) for d in docs]
    evs.append(Event(id="t", type="terms_confirmed", manager_id="m", valid_from=ts, learned_at=ts,
                     payload={"manager_id": "m", "vendor_payment_days": 10,
                              **({"holdback_pct": holdback} if holdback else {})}))
    return evs


def inv(iid, amt, **data):
    return Document(id=f"i-{iid}", kind=SourceKind.vendor_invoice, manager_id="m", vendor_id="v",
                    dated=date(2026, 3, 1), text=f"V. Invoice R-{iid}.",
                    data={"invoice_id": iid, "amount": amt, "received": "2026-03-01", **data})


def fund(iid, amt):
    return Document(id=f"f-{iid}", kind=SourceKind.funding, manager_id="m", dated=date(2026, 3, 2),
                    data={"invoice_id": iid, "amount": amt, "funded_on": "2026-03-02"})


def bank(*pays):
    return Document(id="b", kind=SourceKind.manager_bank, manager_id="m", dated=date(2026, 4, 1),
                    data={"payments": [{"invoice_id": i, "vendor_id": "v", "amount": a, "on": "2026-03-05"} for i, a in pays]})


@pytest.mark.parametrize("seed", range(8))
def test_clean_worlds_reconcile_to_zero(seed):
    evs = generate(seed).events()
    r = reconcile(evs, "keyway")       # the planted unpaid invoice is owed, and still balances
    assert r["owed"] == 1240.0
    assert r["diff"] == 0.0 and round(r["paid"] + r["held"] + r["owed"], 2) == r["funded"]


def test_scenario_3_funded_not_paid_is_owed():
    r = reconcile(generate(n_clean=0).events(), "keyway")
    assert r == {"funded": 1240.0, "paid": 0.0, "held": 0.0, "owed": 1240.0, "unfunded": 0.0,
                 "duplicate_funded": 0.0, "diff": 0.0}


def test_authorized_holdback_is_not_owed():       # scenario 1
    r = reconcile(events(inv("a", 2000.0, inspection="pending"), fund("a", 2000.0),
                         bank(("a", 1800.0)), holdback=10), "m")
    assert (r["paid"], r["held"], r["owed"], r["diff"]) == (1800.0, 200.0, 0.0, 0.0)


def test_holdback_without_pending_inspection_stays_owed():
    r = reconcile(events(inv("a", 2000.0), fund("a", 2000.0), bank(("a", 1800.0)), holdback=10), "m")
    assert (r["held"], r["owed"]) == (0.0, 200.0)


def test_unfunded_is_owner_side_not_manager_owed():   # scenario 6
    r = reconcile(events(inv("a", 9000.0)), "m")
    assert (r["funded"], r["owed"], r["unfunded"], r["diff"]) == (0.0, 0.0, 9000.0, 0.0)


def test_overfunding_and_duplicates_surface_in_diff():
    r = reconcile(events(inv("a", 100.0), fund("a", 150.0), bank(("a", 100.0))), "m")
    assert r["diff"] == 50.0
    first, dup = inv("a", 100.0, vendor_ref="R-1"), inv("b", 100.0, vendor_ref="R-1")
    dup.dated = date(2026, 4, 1); dup.data["received"] = "2026-04-01"
    r = reconcile(events(first, fund("a", 100.0), bank(("a", 100.0)),
                         dup, fund("b", 100.0)), "m")
    assert r["duplicate_funded"] == 100.0 and r["diff"] == 100.0
    assert [l.invoice_id for l in breakdown(events(inv("a", 1.0)), "m")] == ["a"]


def test_owner_signoff_condition_does_not_become_inspection_hold():
    evs = events(inv('a', 2000.0, inspection='pending'), fund('a', 2000.0), bank(('a', 1800.0)), holdback=10)
    evs[-1].payload.update(holdback_condition='until the owner signs off', terms={})
    result = reconcile(evs, 'm')
    assert result['held'] == 0 and result['owed'] == 200
