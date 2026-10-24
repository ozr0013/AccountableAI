"""Documents are stored as `document_ingested` events; this reads them back."""

from __future__ import annotations

from contracts.models import Document, Event


def documents_from_events(events: list[Event]) -> list[Document]:
    return [Document(**e.payload) for e in events if e.type == "document_ingested"]
