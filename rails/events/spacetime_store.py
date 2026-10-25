"""The books in SpacetimeDB: the same append-only event log as rails/events/store.py, with the
same interface, kept in the module's `book_event` table (BOOKS=spacetime).

SpacetimeDB is the system of record. The `append_book_event` reducer assigns each event its
sequence number in commit order and refuses an id re-used for different content, so history can
never be rewritten. Each process keeps a cache of the log and fetches only rows newer than the
last one it has, so reads stay fast and other processes' appends are seen within a moment.

A book is one ledger: 'public' for the real books, 'demo_live' for the demo's copy.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime

from contracts.models import Event, SourceKind

from .store import ConflictingEvent, content_hash

SYNC_INTERVAL_S = 0.25


def _opt(v):
    return {"none": []} if v is None else {"some": v}


def _quote(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


class _Cache:
    """One process-wide copy of a book's log, shared by every store opened on that book."""

    def __init__(self):
        self.lock = threading.RLock()
        self.events: list[Event] = []
        self.by_id: dict[str, tuple[int, str]] = {}      # id -> (seq, content hash)
        self.seq = 0
        self.synced = 0.0


_CACHES: dict[str, _Cache] = {}
_CACHES_LOCK = threading.Lock()


def _cache_for(book: str) -> _Cache:
    with _CACHES_LOCK:
        return _CACHES.setdefault(book, _Cache())


class SpacetimeEventStore:
    conn = None                     # no Postgres connection: code that needs one checks for None

    def __init__(self, book: str = "public", mirror=None):
        if not book.replace("_", "").isalnum():
            raise ValueError("book names are letters, digits and underscores")
        from rails.live.spacetime import SpacetimeMirror
        self.book = book
        self.mirror = mirror or SpacetimeMirror()
        self._c = _cache_for(book)

    # the shared cache, under the names the methods use
    _lock = property(lambda self: self._c.lock)
    _events = property(lambda self: self._c.events)
    _by_id = property(lambda self: self._c.by_id)

    # -- cache ----------------------------------------------------------------------------
    def _sync(self, force: bool = False) -> None:
        c = self._c
        with c.lock:
            if not force and time.monotonic() - c.synced < SYNC_INTERVAL_S:
                return
            rows = self.mirror.query(
                f"SELECT * FROM book_event WHERE book = {_quote(self.book)} AND seq > {c.seq}")
            for r in sorted(rows, key=lambda r: r["seq"]):
                if r["event_id"] in self._by_id:
                    continue
                self._events.append(Event(
                    id=r["event_id"], type=r["kind"], manager_id=r["manager_id"],
                    invoice_id=r["invoice_id"], valid_from=datetime.fromisoformat(r["valid_from"]),
                    learned_at=datetime.fromisoformat(r["learned_at"]), supersedes=r["supersedes"],
                    payload=json.loads(r["payload"]),
                    source=json.loads(r["source"]) if r["source"] else None))
                self._by_id[r["event_id"]] = (r["seq"], r["content_hash"])
                c.seq = max(c.seq, r["seq"])
            c.synced = time.monotonic()

    def forget(self) -> None:
        """Drop the cache (after the book was cleared or replaced)."""
        c = self._c
        with c.lock:
            c.events.clear()
            c.by_id.clear()
            c.seq, c.synced = 0, 0.0

    # -- the EventStore interface ---------------------------------------------------------
    def append(self, event: Event) -> bool:
        """Append an event. Returns False if the identical event is already there (replay)."""
        digest = content_hash(event)
        with self._lock:
            self._sync(force=True)
            if event.id in self._by_id:
                if self._by_id[event.id][1] != digest:
                    raise ConflictingEvent(f"event {event.id} already exists with different content")
                return False
            try:
                self.mirror.call("append_book_event", [
                    self.book, event.id, event.type, _opt(event.manager_id), _opt(event.invoice_id),
                    event.valid_from.isoformat(), event.learned_at.isoformat(), _opt(event.supersedes),
                    json.dumps(event.payload, sort_keys=True, default=str),
                    _opt(json.dumps(event.source.model_dump(mode="json")) if event.source else None),
                    digest])
            except Exception:
                self._sync(force=True)       # another process may have won the race
                if event.id in self._by_id:
                    if self._by_id[event.id][1] != digest:
                        raise ConflictingEvent(f"event {event.id} already exists with different content")
                    return False
                raise
            self._sync(force=True)
            return True

    def get(self, event_id: str) -> Event | None:
        self._sync()
        return next((e for e in self._events if e.id == event_id), None)

    def seq_of(self, event_id: str) -> int | None:
        self._sync()
        hit = self._by_id.get(event_id)
        return hit[0] if hit else None

    def events(self, manager_id=None, invoice_id=None, type=None, as_of=None, known_at=None,
               current_only: bool = False) -> list[Event]:
        """Events in append order, filtered exactly like the PostgreSQL store."""
        self._sync()
        out = [e for e in self._events
               if (manager_id is None or e.manager_id == manager_id)
               and (invoice_id is None or e.invoice_id == invoice_id)
               and (type is None or e.type == type)
               and (as_of is None or e.valid_from <= as_of)
               and (known_at is None or e.learned_at <= known_at)]
        if current_only:
            replaced = {e.supersedes for e in out if e.supersedes}
            out = [e for e in out if e.id not in replaced]
        return out

    def chain(self, event_id: str) -> list[Event]:
        out, cur = [], self.get(event_id)
        while cur:
            out.append(cur)
            cur = self.get(cur.supersedes) if cur.supersedes else None
        return list(reversed(out))

    def clear(self) -> None:
        """Remove the whole book (refused by SpacetimeDB for the real books)."""
        self.mirror.call("clear_book", [self.book])
        self.forget()

    def copy_from(self, book: str) -> None:
        """Replace this book with a copy of another, server side in one transaction."""
        self.mirror.call("copy_book", [book, self.book])
        self.forget()


class SpacetimeDependencyIndex:
    """rails/deps/index.py over SpacetimeDB: facts and the dependencies they imply are derived
    from the event log in memory (they are a pure function of it); only the explicit links a
    workflow records are stored, in `book_dependency`."""

    def __init__(self, store: SpacetimeEventStore):
        self.store = store
        self._facts: dict[str, set[str]] = {}
        self._deps: dict[str, set[str]] = {}            # fact key -> items
        self._indexed: set[str] = set()
        for r in store.mirror.query(f"SELECT * FROM book_dependency WHERE book = {_quote(store.book)}"):
            self._deps.setdefault(r["fact_key"], set()).add(r["item_id"])

    def _fact(self, event_id: str, keys: set[str]) -> None:
        self._facts.setdefault(event_id, set()).update(keys)

    def _dep(self, item_id: str, fact_key: str) -> None:
        self._deps.setdefault(fact_key, set()).add(item_id)

    def record_dependency(self, item_id: str, fact_key: str) -> None:
        self._dep(item_id, fact_key)
        self.store.mirror.call("put_book_dependency", [self.store.book, item_id, fact_key])

    def index_event(self, e: Event) -> None:
        from ..facts import INVOICE_ATTRS, TERMS_ATTRS, fact_keys
        self._fact(e.id, fact_keys(e))
        self._indexed.add(e.id)
        if e.type == "document_ingested" and e.payload.get("kind") == SourceKind.vendor_invoice:
            if iid := e.payload.get("data", {}).get("invoice_id"):
                for a in INVOICE_ATTRS:
                    self._dep(iid, f"invoice:{iid}.{a}")
                if manager := e.payload.get("manager_id"):
                    for a in TERMS_ATTRS:
                        self._dep(iid, f"terms:{manager}.{a}")

    def rebuild(self, events: list[Event]) -> None:
        """The same derivation as the PostgreSQL index (payment and duplicate links included)."""
        from ..deps.index import _DUP_ATTRS
        from ..docs import documents_from_events
        from ..matcher import find_duplicates, gather_evidence
        self._facts, self._indexed = {}, set()
        for e in events:
            self.index_event(e)
        docs = documents_from_events(events)
        by_doc = {e.payload.get("id"): e.id for e in events if e.type == "document_ingested"}
        for iid, ev in gather_evidence(docs).evidence.items():
            cites = ev.cites.get("bank", []) + ([c for _, c in ev.bank_docs] if not ev.bank_cents else [])
            for c in cites:
                if c.source_id in by_doc:
                    self._fact(by_doc[c.source_id], {f"invoice:{iid}.payment"})
        for dup, orig in find_duplicates(docs).items():
            for a in _DUP_ATTRS:
                self._dep(dup, f"invoice:{orig}.{a}")
                self._dep(orig, f"invoice:{dup}.{a}")

    def _catch_up(self) -> None:
        """Index events other processes appended since we last looked."""
        events = self.store.events()
        if any(e.id not in self._indexed for e in events):
            self.rebuild(events)

    def keys_for(self, x: str) -> set[str]:
        self._catch_up()
        return self._facts.get(x) or {x}

    def dependents_of(self, x: str) -> list[str]:
        keys = self.keys_for(x)
        return sorted({item for k in keys for item in self._deps.get(k, ())})

    def fact_rows(self) -> list[tuple[str, str]]:
        self._catch_up()
        return [(e, k) for e, ks in self._facts.items() for k in ks]

    def dependency_rows(self) -> list[tuple[str, str]]:
        self._catch_up()
        return [(i, k) for k, items in self._deps.items() for i in items]
