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


def test_same_instant_in_any_offset_is_the_same_event(store):
    from datetime import timedelta
    pacific = timezone(timedelta(hours=-7))
    a = ev("tz", valid=(4, 22), learned=(4, 22))
    b = a.model_copy(update={"valid_from": a.valid_from.astimezone(pacific),
                             "learned_at": a.learned_at.astimezone(pacific)})
    assert store.append(a) is True
    assert store.append(b) is False          # not a ConflictingEvent


def test_event_read_back_from_a_non_utc_session_can_be_saved_again(schema):
    """A database whose clock is not UTC (a teammate's Homebrew Postgres) must not break replay."""
    import psycopg
    from rails.db import database_url
    conn = psycopg.connect(database_url(), autocommit=True, options="-c timezone=America/Los_Angeles")
    conn.execute(f'SET search_path TO "{schema}"')
    st = EventStore(conn=conn)               # bypasses rails.db.connect on purpose
    st.append(ev("pac", valid=(4, 22), learned=(4, 22)))
    back = st.get("pac")
    assert back.valid_from.utcoffset().total_seconds() != 0        # really read back in Pacific
    assert st.append(back) is False


def test_connect_pins_the_session_to_utc(schema):
    from rails.db import connect
    assert connect().execute("SHOW TIME ZONE").fetchone()[0] == "UTC"
