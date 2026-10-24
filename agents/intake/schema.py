"""Local strict extraction contract; shared contracts stay untouched."""
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["invoice", "funding", "payment", "statement", "claim", "terms"]
    invoice_id: str | None = None
    vendor_id: str | None = None
    payer: str | None = None
    payee: str | None = None
    amount: Decimal | None = None
    on: date | None = None
    status: Literal["paid", "not_received", "hold_missing_w9", "bank_change_requested", "claim"] | None = None
    received: date | None = None
    vendor_ref: str | None = None
    service_period: str | None = None
    inspection: Literal["pending", "passed"] | None = None
    transaction_id: str | None = None
    line_items: list[Decimal] = Field(default_factory=list)
    vendor_payment_days: int | None = None
    holdback_pct: int | None = None
    approval_limit: Decimal | None = None
    # Every populated semantic field must have its own exact source excerpt.
    quotes: dict[str, str]
    confidence: float = Field(ge=0, le=1)
    new_entity: bool = False


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[Candidate]
    uncertain: list[str] = Field(default_factory=list)


# The provider transport has fixed fields and a list of named excerpts. Dynamic
# dictionaries and many optional JSON keys can exceed structured-output grammars.
# Internal validation still uses Candidate; all transport values pass through it.
from typing import Annotated

from pydantic import create_model

SEMANTIC_FIELDS = ("invoice_id", "vendor_id", "payer", "payee", "amount", "on", "status", "received",
                   "vendor_ref", "service_period", "inspection", "transaction_id", "line_items",
                   "vendor_payment_days", "holdback_pct", "approval_limit")


class FieldQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal[SEMANTIC_FIELDS]
    text: str


ModelCandidate = create_model(
    "ModelCandidate", __config__=ConfigDict(extra="forbid"),
    **{name: ((Annotated[f.annotation, *f.metadata] if f.metadata else f.annotation), ...)
       for name, f in Candidate.model_fields.items() if name != "quotes"},
    quotes=(list[FieldQuote], ...),
)


class ModelExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[ModelCandidate]
    uncertain: list[str]

    def checked_shape(self):
        candidates = []
        for c in self.candidates:
            if len({q.field for q in c.quotes}) != len(c.quotes):
                raise ValueError("duplicate quote field")
            payload = c.model_dump()
            payload["quotes"] = {q.field: q.text for q in c.quotes}
            candidates.append(Candidate.model_validate(payload))
        return Extraction(candidates=candidates, uncertain=self.uncertain)
