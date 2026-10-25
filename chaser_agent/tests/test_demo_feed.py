"""The demo's daily paperwork: same month every run, every kind of trouble present."""
from datetime import date

from chaser_agent import demo_feed

VENDORS = [demo_feed.Vendor(f"v-{i}", f"Vendor {i}", "Repair service", 1000.0 + 100 * i, {})
           for i in range(12)]
START, END = date(2026, 4, 22), date(2026, 5, 12)


def _all(feed):
    return [d for docs in feed.values() for d in docs]


def test_the_feed_is_the_same_every_run():
    a = [(d.id, d.dated) for d in _all(demo_feed.build(VENDORS, START, END))]
    b = [(d.id, d.dated) for d in _all(demo_feed.build(VENDORS, START, END))]
    assert a == b and len(a) > 100


def test_every_kind_of_trouble_is_planted_and_dated_inside_the_window():
    feed = demo_feed.build(VENDORS, START, END)
    fates = set(demo_feed.build.fates.values())
    assert {"on_time", "late", "unpaid", "partial", "statement_amount"} <= fates
    assert all(START <= day <= END for day in feed)
    statements = [d for d in _all(feed) if d.kind.value == "manager_statement"]
    assert len(statements) == 1
    invoices = {d.data["invoice_id"]: d.data["amount"] for d in _all(feed) if d.kind.value == "vendor_invoice"}
    differs = [l for l in statements[0].data["lines"] if l["amount"] != invoices[l["invoice_id"]]]
    assert len(differs) == 2


def test_bank_payments_post_on_weekdays():
    for d in _all(demo_feed.build(VENDORS, START, END)):
        if d.kind.value == "manager_bank":
            assert all(date.fromisoformat(p["on"]).weekday() < 5 for p in d.data["payments"])
