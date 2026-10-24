"""Shapes the agreement agent reads and writes. Standalone: nothing else in the repo imports these."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DocKind = Literal["agreement", "invoice", "other"]
FieldStatus = Literal["found", "missing", "unclear"]


class SourceDoc(BaseModel):
    """One file to review, wherever it came from (Gmail attachment, local folder)."""

    id: str
    source: str                          # "gmail" | "local"
    filename: str
    mime: str
    data: bytes = Field(repr=False)
    sender: str | None = None            # email From, if it came by email
    subject: str | None = None
    received_at: datetime | None = None
    message_id: str | None = None        # Gmail message id, for linking back


class Section(BaseModel):
    number: str                          # "4", "4.2", or "" for an unnumbered block
    title: str
    text: str
    page: int


class Parsed(BaseModel):
    doc_id: str
    text: str
    pages: list[str]
    header: list[str]                    # the first lines: title, parties, date
    sections: list[Section]
    kind: DocKind = "other"
    kind_reason: str = ""

    def where(self, quote: str) -> str | None:
        """Section number (or page) containing the quote, case- and space-insensitive."""
        q = norm(quote)
        for s in self.sections:
            if q and q in norm(s.text):
                return f"§{s.number}" if s.number else f"p.{s.page}"
        for i, p in enumerate(self.pages, 1):
            if q and q in norm(p):
                return f"p.{i}"
        return None


class FieldResult(BaseModel):
    name: str                            # "vendor_name", "payment_due", ...
    label: str                           # "Vendor company name"
    status: FieldStatus
    value: str | None = None
    quote: str | None = None             # verbatim text from the document backing the value
    location: str | None = None          # "§4.2", "p.1", "header"
    note: str = ""                       # why missing / unclear, what was searched
    by: Literal["rules", "model"] = "rules"


class Escalation(BaseModel):
    """A notice for the owner. Drafted only: nothing is sent without the owner's approval."""

    id: str
    doc_id: str
    to: str = "owner"
    subject: str
    body: str
    items: list[FieldResult]
    severity: Literal["high", "medium"]
    status: Literal["drafted", "sent", "resolved"] = "drafted"
    created_at: datetime


class Review(BaseModel):
    doc_id: str
    filename: str
    source: str
    sender: str | None = None
    subject: str | None = None
    kind: DocKind
    kind_reason: str
    relevant: bool = True                # False: not an agreement or invoice, skipped
    fields: list[FieldResult]
    complete: bool
    escalation: Escalation | None = None
    steps: list[str] = Field(default_factory=list)    # what the agent did, in order
    mode: Literal["rules", "model"] = "rules"
    cost_usd: float = 0.0


def norm(s: str) -> str:
    import re
    return re.sub(r"\s+", " ", (s or "").replace("’", "'").replace("“", '"')
                  .replace("”", '"')).strip().lower()
