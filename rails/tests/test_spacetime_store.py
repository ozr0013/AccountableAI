"""The books in SpacetimeDB behave exactly like the PostgreSQL event store.

A fake of the module's reducers (spacetime/spacetimedb/src/index.ts) stands in for the server, so
these run offline; the reducer rules they mirror are: seq assigned per book in commit order, an
identical re-send is a no-op, an id re-used for different content is refused, 'public' can never
be cleared or overwritten.
"""
import re
from datetime import UTC, datetime

import pytest

from contracts.models import Event
from rails.events import spacetime_store
from rails.events.spacetime_store import SpacetimeDependencyIndex, SpacetimeEventStore
from rails.events.store import ConflictingEvent


class FakeSpacetime:
    def __init__(self):
        self.rows, self.counter, self.deps = {}, {}, {}

    def call(self, reducer, args):
        if reducer == "append_book_event":
            book, eid = args[0], args[1]
            key = f"{book}|{eid}"
            if key in self.rows:
                if self.rows[key]["content_hash"] != args[10]:
                    raise RuntimeError("HTTP Error 530: conflict")
                return
            seq = self.counter.get(book, 0) + 1
            self.counter[book] = seq
            opt = lambda v: v.get("some") if isinstance(v, dict) else v  # noqa: E731
            self.rows[key] = {"book": book, "seq": seq, "event_id": eid, "kind": args[2],
                              "manager_id": opt(args[3]), "invoice_id": opt(args[4]), "valid_from": args[5],
                              "learned_at": args[6], "supersedes": opt(args[7]), "payload": args[8],
                              "source": opt(args[9]), "content_hash": args[10]}
        elif reducer == "clear_book":
            if args[0] == "public":
                raise RuntimeError("HTTP Error 530: the real books cannot be cleared")
            self.rows = {k: r for k, r in self.rows.items() if r["book"] != args[0]}
            self.counter.pop(args[0], None)
        elif reducer == "copy_book":
            src, dst = args
            if dst == "public":
                raise RuntimeError("HTTP Error 530")
            self.rows = {k: r for k, r in self.rows.items() if r["book"] != dst}
            for r in [r for r in self.rows.values() if r["book"] == src]:
                self.rows[f"{dst}|{r['event_id']}"] = {**r, "book": dst}
            self.counter[dst] = self.counter.get(src, 0)
        elif reducer == "put_book_dependency":
            self.deps[f"{args[0]}|{args[1]}|{args[2]}"] = {"book": args[0], "item_id": args[1], "fact_key": args[2]}

    def query(self, sql):
        book = re.search(r"book = '([^']*)'", sql).group(1)
        if "book_dependency" in sql:
            return [d for d in self.deps.values() if d["book"] == book]
        after = int(re.search(r"seq > (\d+)", sql).group(1))
        return [r for r in self.rows.values() if r["book"] == book and r["seq"] > after]


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(spacetime_store, "_CACHES", {})
    monkeypatch.setattr(spacetime_store, "SYNC_INTERVAL_S", 0)
    return FakeSpacetime()


def _ev(i, **kw):
    t = datetime(2026, 1, i, 9, tzinfo=UTC)
    return Event(id=f"ev-{i}", type=kw.pop("type", "fact_recorded"), manager_id="keyway", valid_from=t,
                 learned_at=t, payload=kw.pop("payload", {"n": i}), **kw)


def test_append_is_ordered_idempotent_and_never_rewrites_history(server):
    store = SpacetimeEventStore("public", mirror=server)
    assert store.append(_ev(1)) and store.append(_ev(2))
    assert store.append(_ev(1)) is False                      # identical replay
    with pytest.raises(ConflictingEvent):
        store.append(_ev(1, payload={"n": 999}))             # same id, different content
    assert [e.id for e in store.events()] == ["ev-1", "ev-2"]
    assert store.get("ev-2").payload == {"n": 2} and store.seq_of("ev-2") == 2


def test_filters_and_corrections_match_the_postgres_store(server):
    store = SpacetimeEventStore("public", mirror=server)
    store.append(_ev(1))
    store.append(_ev(3, supersedes="ev-1", payload={"n": 1, "fixed": True}))
    assert [e.id for e in store.events(current_only=True)] == ["ev-3"]
    assert [e.id for e in store.events(as_of=datetime(2026, 1, 2, tzinfo=UTC))] == ["ev-1"]
    assert [e.id for e in store.chain("ev-3")] == ["ev-1", "ev-3"]


def test_a_second_process_sees_appends_and_stores_share_a_cache(server):
    a = SpacetimeEventStore("public", mirror=server)
    a.append(_ev(1))
    spacetime_store._CACHES.clear()                          # a different process: cold cache
    b = SpacetimeEventStore("public", mirror=server)
    assert [e.id for e in b.events()] == ["ev-1"]
    a.append(_ev(2))
    assert [e.id for e in b.events()] == ["ev-1", "ev-2"]


def test_the_demo_book_is_a_copy_and_the_real_books_cannot_be_cleared(server):
    real = SpacetimeEventStore("public", mirror=server)
    real.append(_ev(1))
    demo = SpacetimeEventStore("demo_live", mirror=server)
    demo.copy_from("public")
    demo.append(_ev(2))
    assert [e.id for e in demo.events()] == ["ev-1", "ev-2"] and [e.id for e in real.events()] == ["ev-1"]
    with pytest.raises(RuntimeError):
        real.clear()
    demo.clear()
    assert demo.events() == [] and len(real.events()) == 1


def test_dependencies_follow_the_events_and_explicit_links_persist(server):
    store = SpacetimeEventStore("public", mirror=server)
    store.append(_ev(4, type="document_ingested", payload={
        "id": "doc-inv-1", "kind": "vendor_invoice", "manager_id": "keyway", "vendor_id": "v-a",
        "dated": "2026-01-04", "data": {"invoice_id": "INV-1", "amount": 10, "received": "2026-01-04"}}))
    deps = SpacetimeDependencyIndex(store)
    assert "INV-1" in deps.dependents_of("invoice:INV-1.amount")
    deps.record_dependency("alert-7", "invoice:INV-1.amount")
    fresh = SpacetimeDependencyIndex(store)
    assert "alert-7" in fresh.dependents_of("invoice:INV-1.amount")
