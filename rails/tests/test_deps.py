from datetime import date, datetime, timezone

import pytest

from contracts.models import Document, Event, SourceKind
from rails.aging import aging, late_months
from rails.deps import DependencyIndex
from rails.events import EventStore
from rails.events.ingest import document_event
from rails.ledger import build_ledger
from rails.recon import reconcile

AS_OF = date(2026, 4, 25)
TS = datetime(2026, 1, 2, tzinfo=timezone.utc)


def inv(iid, vendor, amount, received, **data):
    return Document(id=f"i-{iid}", kind=SourceKind.vendor_invoice, manager_id="keyway",
                    vendor_id=vendor, dated=date.fromisoformat(received),
                    text=f"V. Invoice R-{iid}.",
                    data={"invoice_id": iid, "amount": amount, "received": received, **data})


def fund(iid, amount):
    return Document(id=f"f-{iid}", kind=SourceKind.funding, manager_id="keyway",
                    dated=date(2026, 3, 25),
                    data={"invoice_id": iid, "amount": amount, "funded_on": "2026-03-25"})


def moment_world():
    """Keyway owes three vendors $3,140 (README moment). The electrician invoice is dated Mar 28,
    so on the face of it it is overdue; it actually arrived Apr 1 (due Apr 11), paid Apr 8."""
    docs = [
        inv("INV-KW-0301", "v-plumb", 1240.0, "2026-03-03"), fund("INV-KW-0301", 1240.0),
        inv("INV-KW-0402", "v-elec", 900.0, "2026-03-28"), fund("INV-KW-0402", 900.0),
        inv("INV-KW-0403", "v-hvac", 1000.0, "2026-03-05"), fund("INV-KW-0403", 1000.0),
        Document(id="bank-kw", kind=SourceKind.manager_bank, manager_id="keyway",
                 dated=date(2026, 4, 20), data={"payments": []}),
    ]
    evs = [document_event(d) for d in docs]
    evs.append(Event(id="t", type="terms_confirmed", manager_id="keyway", valid_from=TS,
                     learned_at=TS, payload={"manager_id": "keyway", "vendor_payment_days": 10}))
    return evs


@pytest.fixture
def parts(schema):
    store = EventStore(schema=schema)
    deps = DependencyIndex(conn=store.conn)
    return store, deps


def correction(valid="2026-04-01"):
    return Event(id="ev-judge-apr1", type="fact_corrected", manager_id="keyway",
                 invoice_id="INV-KW-0402",
                 valid_from=datetime(2026, 4, 1, tzinfo=timezone.utc),
                 learned_at=datetime(2026, 4, 25, 12, tzinfo=timezone.utc),
                 payload={"fact_key": "invoice:INV-KW-0402.received", "value": valid})


def test_the_moment_end_to_end(parts):
    store, deps = parts
    for e in moment_world():
        store.append(e)
        deps.index_event(e)
    # Electrician looks late before the correction; all three are owed.
    before = store.events(current_only=True)
    assert reconcile(before, "keyway")["owed"] == 3140.0

    c = correction()
    store.append(c)
    deps.index_event(c)
    # Selective recheck: only the electrician's invoice, no event-id lookup of any prior arrival.
    assert deps.dependents_of(c.id) == ["INV-KW-0402"]
    assert deps.dependents_of("invoice:INV-KW-0402.received") == ["INV-KW-0402"]

    after = store.events(current_only=True)
    lines = {l.invoice_id: l for l in build_ledger(after)}
    assert lines["INV-KW-0402"].invoice_received == date(2026, 4, 1)
    assert lines["INV-KW-0402"].due_date == date(2026, 4, 11)
    assert lines["INV-KW-0301"].due_date == date(2026, 3, 13)          # untouched
    assert lines["INV-KW-0403"].due_date == date(2026, 3, 15)
    # As of Apr 8: before the correction the electrician looks overdue, after it it is current.
    apr8 = date(2026, 4, 8)
    assert aging(before, "keyway", apr8)["1-30"] == 3140.0
    a = aging(after, "keyway", apr8)
    assert a["current"] == 900.0 and a["1-30"] == 2240.0     # the other two stay owed
    assert late_months(before, "keyway", apr8)["2026-03"] is True
    assert late_months(after, "keyway", apr8)["2026-03"] is True     # the plumber and HVAC are still late
    assert "INV-KW-0402" not in {i for ids in _late(after, apr8).values() for i in ids}


def _late(events, as_of):
    from rails.aging.aging import _late_invoices
    return _late_invoices(events, "keyway", as_of)


def test_replay_without_correction_does_not_see_it(parts):
    store, deps = parts
    for e in moment_world() + [correction()]:
        store.append(e)
    known_then = store.events(known_at=datetime(2026, 4, 20, tzinfo=timezone.utc))
    line = {l.invoice_id: l for l in build_ledger(known_then)}["INV-KW-0402"]
    assert line.invoice_received == date(2026, 3, 28)


def test_terms_change_affects_every_invoice_of_that_manager(parts):
    store, deps = parts
    for e in moment_world():
        store.append(e)
        deps.index_event(e)
    assert deps.dependents_of("terms:keyway.vendor_payment_days") == [
        "INV-KW-0301", "INV-KW-0402", "INV-KW-0403"]
    assert deps.dependents_of("terms:othermgr.vendor_payment_days") == []


def test_rebuild_matches_incremental_and_ties_unlabelled_payment(parts):
    store, deps = parts
    evs = moment_world()
    bank = Document(id="bank-2", kind=SourceKind.manager_bank, manager_id="keyway",
                    dated=date(2026, 4, 20),
                    data={"payments": [{"vendor_id": "v-elec", "amount": 900.0, "on": "2026-04-08"}]})
    evs.append(document_event(bank))
    for e in evs:
        store.append(e)
        deps.index_event(e)
    inc = {r for r in deps.conn.execute("SELECT * FROM dependencies").fetchall()}
    deps.rebuild(store.events(current_only=True))
    assert inc <= set(deps.conn.execute("SELECT * FROM dependencies").fetchall())
    # matched without an invoice id, and it also reports "no payment" for the two open items
    assert deps.dependents_of("ev-doc-bank-2") == ["INV-KW-0301", "INV-KW-0402", "INV-KW-0403"]
    assert "INV-KW-0402" in deps.dependents_of("invoice:INV-KW-0402.payment")


def test_explicit_dependency_for_alert_items(parts):
    _, deps = parts
    deps.record_dependency("alert-pattern-keyway", "invoice:INV-KW-0402.received")
    assert "alert-pattern-keyway" in deps.dependents_of("invoice:INV-KW-0402.received")


def test_bank_record_without_payment_is_tied_to_open_items_only(parts):
    store, deps = parts
    evs = moment_world()
    for e in evs:
        store.append(e)
    deps.rebuild(store.events(current_only=True))
    assert deps.dependents_of("ev-doc-bank-kw") == ["INV-KW-0301", "INV-KW-0402", "INV-KW-0403"]
