from datetime import date, datetime, timezone

from contracts.models import Document, Event, SourceKind
from rails.aging import aging, late_months, pattern
from rails.events.ingest import document_event
from world import generate

AS_OF = date(2026, 6, 30)


def invoice(iid, received, amount=100.0, paid_on=None, funded=True, manager="m"):
    r = date.fromisoformat(received)
    docs = [Document(id=f"i-{iid}", kind=SourceKind.vendor_invoice, manager_id=manager,
                     vendor_id="v", dated=r, text=f"V. Invoice R-{iid}.",
                     data={"invoice_id": iid, "amount": amount, "received": received})]
    if funded:
        docs.append(Document(id=f"f-{iid}", kind=SourceKind.funding, manager_id=manager, dated=r,
                             data={"invoice_id": iid, "amount": amount, "funded_on": received}))
    if paid_on:
        docs.append(Document(id=f"b-{iid}", kind=SourceKind.manager_bank, manager_id=manager,
                             dated=date.fromisoformat(paid_on),
                             data={"payments": [{"invoice_id": iid, "amount": amount, "on": paid_on}]}))
    return docs


def world(*invoices):
    ts = datetime(2026, 1, 2, tzinfo=timezone.utc)
    evs = [document_event(d) for docs in invoices for d in docs]
    evs.append(Event(id="t", type="terms_confirmed", manager_id="m", valid_from=ts, learned_at=ts,
                     payload={"manager_id": "m", "vendor_payment_days": 10}))
    return evs


def test_clean_world_has_no_lateness():
    evs = generate().events()           # scenario 3 only: just the planted plumber is late
    lm = late_months(evs, "keyway", date(2026, 4, 22))
    assert lm == {"2026-01": False, "2026-02": False, "2026-03": True, "2026-04": False}
    p = pattern(evs, "keyway", date(2026, 4, 22))
    assert not p.recurring and p.payment_window_days == 10


def test_unpaid_planted_invoice_makes_march_late_for_keyway():
    evs = generate(n_clean=0).events()
    assert late_months(evs, "keyway", date(2026, 4, 25)) == {"2026-03": True}
    a = aging(evs, "keyway", date(2026, 4, 25))      # due Mar 13 -> 43 days past due
    assert a["31-60"] == 1240.0 and sum(a.values()) == 1240.0


def test_pattern_late_4_of_6_is_recurring_with_history():       # scenario 4
    evs = world(
        invoice("a", "2026-01-05", paid_on="2026-01-12"),            # on time
        invoice("b", "2026-02-05", paid_on="2026-02-25"),            # late
        invoice("c", "2026-03-05", paid_on="2026-03-28"),            # late
        invoice("d", "2026-04-05", paid_on="2026-04-10"),            # on time
        invoice("e", "2026-05-05", paid_on="2026-05-30"),            # late
        invoice("f", "2026-06-05", paid_on="2026-06-27"))            # late
    p = pattern(evs, "m", AS_OF)
    assert (p.months, p.late, p.recurring) == (6, 4, True)
    assert [h["month"] for h in p.history if h["late"]] == ["2026-02", "2026-03", "2026-05", "2026-06"]


def test_one_late_month_is_not_a_pattern():
    evs = world(*[invoice(c, f"2026-0{i}-05", paid_on=f"2026-0{i}-{'25' if i == 3 else '10'}")
                  for i, c in enumerate("abcdef", start=1)])
    p = pattern(evs, "m", AS_OF)
    assert p.late == 1 and not p.recurring


def test_owner_side_and_holds_do_not_count_against_manager():
    evs = world(invoice("big", "2026-03-05", amount=9000.0, funded=False))   # scenario 6
    assert late_months(evs, "m", AS_OF) == {}
    assert aging(evs, "m", AS_OF)["90+"] == 0
