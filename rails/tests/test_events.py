import json
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest

from contracts.models import Document, Event
from rails.events import ConflictingEvent, EventStore, ingest_documents

FIXTURE = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "sample"


def ts(m, d):
    return datetime(2026, m, d, tzinfo=timezone.utc)


def ev(id, valid=(3, 1), learned=(3, 1), **kw):
    return Event(id=id, type=kw.pop("type", "fact"), valid_from=ts(*valid),
                 learned_at=ts(*learned), **kw)


@pytest.fixture
def store(schema):
    return EventStore(schema=schema)


def test_append_only_blocks_update_delete_truncate(store):
    store.append(ev("a"))
    for sql in ("UPDATE events SET type='x'", "DELETE FROM events", "TRUNCATE events"):
        with pytest.raises(psycopg.errors.RaiseException):
            store.conn.execute(sql)
    assert len(store.events()) == 1


def test_replay_is_idempotent_and_conflicts_are_loud(store):
    assert store.append(ev("a")) is True
    assert store.append(ev("a")) is False
    with pytest.raises(ConflictingEvent):
        store.append(ev("a", payload={"x": 1}))
    assert len(store.events()) == 1


def test_supersede_chain_and_current_view(store):
    store.append(ev("arrive-1", type="invoice_received", invoice_id="i1",
                    payload={"received": "2026-03-20"}))
    store.append(ev("arrive-2", valid=(3, 20), learned=(4, 25), type="invoice_received",
                    invoice_id="i1", payload={"received": "2026-04-01"}, supersedes="arrive-1"))
    assert [e.id for e in store.chain("arrive-2")] == ["arrive-1", "arrive-2"]
    assert [e.id for e in store.events(invoice_id="i1", current_only=True)] == ["arrive-2"]
    assert len(store.events(invoice_id="i1")) == 2  # history is never lost


def test_supersedes_must_reference_existing_event(store):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        store.append(ev("b", supersedes="nope"))


def test_two_timelines(store):
    store.append(ev("early", valid=(3, 1), learned=(3, 2)))
    store.append(ev("late-learned", valid=(3, 5), learned=(4, 25)))
    ids = lambda **kw: [e.id for e in store.events(**kw)]
    assert ids(as_of=ts(3, 10)) == ["early", "late-learned"]       # true by Mar 10
    assert ids(known_at=ts(3, 10)) == ["early"]                    # but we only knew one then
    assert ids(as_of=ts(3, 3), known_at=ts(4, 30)) == ["early"]


def test_ingest_fixture_documents_replay_safe(store):
    docs = [Document(**d) for d in json.loads((FIXTURE / "documents.json").read_text())]
    assert ingest_documents(store, docs) == len(docs)
    assert ingest_documents(store, docs) == 0
    assert len(store.events(type="document_ingested")) == len(docs)
    assert len(store.events(manager_id="keyway")) == len(docs)
