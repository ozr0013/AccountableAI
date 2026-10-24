"""Documents are stored as `document_ingested` events; this reads them back.

Fact corrections are applied, and each `chase_reply` event becomes a manager_reply document
per invoice it covers (so the matcher and trust rules see it like any other source).
"""

from __future__ import annotations

from contracts.models import Document, Event, SourceKind

from .facts import effective


def documents_from_events(events: list[Event]) -> list[Document]:
    docs = []
    for e in effective(events):
        if e.type == "document_ingested":
            docs.append(Document(**e.payload))
        elif e.type == "chase_reply":
            p = e.payload
            for iid in p.get("invoice_ids", []):
                docs.append(Document(
                    id=f"{p.get('id', e.id)}:{iid}", kind=SourceKind.manager_reply,
                    manager_id=e.manager_id or p.get("manager_id"), dated=e.valid_from.date(),
                    text=p.get("text", ""),
                    data={"invoice_id": iid, "status": "paid" if p.get("claims_paid") else "other",
                          "reply_id": p.get("id", e.id)}))
    return docs
