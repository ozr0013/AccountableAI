"""Ingestion: turn raw documents into events. Deterministic ids make replay a no-op."""

from __future__ import annotations

from datetime import datetime, time, timezone

from contracts.models import Citation, Document, Event

from .store import EventStore


def _dt(d) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def document_event(doc: Document, learned_at: datetime | None = None) -> Event:
    """document_ingested event; valid_from = document date, learned_at = when we received it."""
    valid = _dt(doc.dated) if doc.dated else (learned_at or datetime.now(timezone.utc))
    return Event(
        id=f"ev-doc-{doc.id}",
        type="document_ingested",
        manager_id=doc.manager_id,
        invoice_id=doc.data.get("invoice_id"),
        valid_from=valid,
        learned_at=learned_at or valid,
        payload=doc.model_dump(mode="json"),
        source=Citation(source_id=doc.id, kind=doc.kind),
    )


def ingest_documents(
    store: EventStore, docs: list[Document], learned_at: datetime | None = None
) -> int:
    """Append one event per document. Returns how many were new."""
    return sum(store.append(document_event(d, learned_at)) for d in docs)
