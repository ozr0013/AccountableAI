"""Versioned case projections with atomic, append-only audit events."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
from typing import Literal

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from contracts.models import Event, LedgerLine
from rails.cases import aware, connection, identity, memory_lock


class CaseConflict(RuntimeError):
    """A writer used a stale case version."""


class AgentCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: Literal["invoice", "agreement"] = "invoice"
    owner_id: str = "oakline"
    manager_id: str
    vendor_id: str
    invoice_id: str
    state: Literal[
        "awaiting_terms",
        "monitoring",
        "awaiting_payment_proof",
        "awaiting_statement",
        "awaiting_human",
        "escalated",
        "resolved",
        "error",
    ] = "monitoring"
    version: int = Field(default=1, ge=1)
    plan: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    next_action: str | None = None
    next_wake_at: datetime | None = None
    summary: str = ""
    agreement_version: str | None = None
    pending_approval: str | None = None
    last_observation: dict = Field(default_factory=dict)
    updated_at: datetime

    @field_validator("next_wake_at", "updated_at")
    @classmethod
    def timezone_required(cls, value):
        return aware(value) if value is not None else None


class CaseStore:
    def __init__(self, rails):
        self.rails = rails
        self.conn = connection(rails)
        self.lock = memory_lock(rails)

    def _transaction(self):
        return self.conn.transaction() if self.conn is not None else nullcontext()

    def ensure(self, line: LedgerLine, now: datetime, owner_id="oakline", kind="invoice") -> AgentCase:
        aware(now)
        case_id = "case_" + identity(owner_id, line.manager_id, line.vendor_id, line.invoice_id)
        with self.lock, self._transaction():
            case = AgentCase(
                id=case_id,
                kind=kind,
                owner_id=owner_id,
                manager_id=line.manager_id,
                vendor_id=line.vendor_id,
                invoice_id=line.invoice_id,
                updated_at=now,
            )
            if self.conn is not None:
                inserted = self.conn.execute(
                    "INSERT INTO agent_cases VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                    (
                        case.id,
                        case.owner_id,
                        case.manager_id,
                        case.vendor_id,
                        case.invoice_id,
                        case.version,
                        Jsonb(case.model_dump(mode="json")),
                    ),
                ).fetchone()
                if not inserted:
                    return self.get(case_id)
            else:
                existing = self._memory_cases().get(case_id)
                if existing:
                    return existing
            self._changed(case, now)
            return case

    def _memory_cases(self):
        return {
            e.payload["case"]["id"]: AgentCase.model_validate(e.payload["case"])
            for e in self.rails.events()
            if e.type == "case_changed"
        }

    def get(self, case_id: str) -> AgentCase:
        if self.conn is not None:
            row = self.conn.execute(
                "SELECT body FROM agent_cases WHERE id=%s", (case_id,)
            ).fetchone()
            if row:
                return AgentCase.model_validate(row[0])
        else:
            case = self._memory_cases().get(case_id)
            if case:
                return case
        raise KeyError(case_id)

    def list(self, manager_id=None) -> list[AgentCase]:
        if self.conn is not None:
            rows = self.conn.execute("SELECT body FROM agent_cases ORDER BY id").fetchall()
            cases = [AgentCase.model_validate(row[0]) for row in rows]
        else:
            cases = sorted(self._memory_cases().values(), key=lambda c: c.id)
        return [c for c in cases if manager_id is None or c.manager_id == manager_id]

    def update(self, case_id, expected_version, now, **changes) -> AgentCase:
        aware(now)
        immutable = {
            "id",
            "owner_id",
            "kind",
            "manager_id",
            "vendor_id",
            "invoice_id",
            "version",
            "updated_at",
        }
        if immutable.intersection(changes):
            raise ValueError("case scope/version/timestamp are immutable")
        with self.lock, self._transaction():
            old = self.get(case_id)
            if old.version != expected_version:
                raise CaseConflict(case_id)
            if (
                old.state == "resolved"
                and changes.get("state", old.state) != "resolved"
                and (
                    changes.get("state") != "monitoring"
                    or not changes.get("last_observation")
                    or changes["last_observation"] == old.last_observation
                )
            ):
                raise ValueError(
                    "resolved case requires new evidence and explicit monitoring reopen"
                )
            if now < old.updated_at:
                raise ValueError("case update timestamp cannot move backwards")
            case = AgentCase.model_validate(
                {**old.model_dump(), **changes, "version": old.version + 1, "updated_at": now}
            )
            if self.conn is not None:
                row = self.conn.execute(
                    "UPDATE agent_cases SET version=%s,body=%s WHERE id=%s AND version=%s RETURNING id",
                    (case.version, Jsonb(case.model_dump(mode="json")), case_id, expected_version),
                ).fetchone()
                if not row:
                    raise CaseConflict(case_id)
            self._changed(case, now)
            return case

    def _changed(self, case, now):
        event = Event(
            id=f"{case.id}:v{case.version}",
            type="case_changed",
            manager_id=case.manager_id,
            invoice_id=case.invoice_id,
            valid_from=now,
            learned_at=now,
            payload={"case_id": case.id, "case": case.model_dump(mode="json")},
        )
        if self.conn is not None:
            self.rails.store.append(event)
        else:
            self.rails.append_event(event)

    def record(self, case_id, kind, payload, now, key) -> Event:
        aware(now)
        if kind in {"case_changed", "job_changed"}:
            raise ValueError("reserved audit event kind")
        with self.lock, self._transaction():
            if self.conn is not None:
                self.conn.execute("SELECT id FROM agent_cases WHERE id=%s FOR UPDATE", (case_id,))
            case = self.get(case_id)
            event_id = "case_event_" + identity(case_id, key)
            existing = next(
                (e for e in self.rails.events(manager_id=case.manager_id) if e.id == event_id), None
            )
            body = {"case_id": case_id, **payload}
            body["case_id"] = case_id
            if existing:
                if existing.type != kind or existing.payload != body:
                    raise ValueError("record key already used with different content")
                return existing
            event = Event(
                id=event_id,
                type=kind,
                manager_id=case.manager_id,
                invoice_id=case.invoice_id,
                valid_from=now,
                learned_at=now,
                payload=body,
            )
            if self.conn is not None:
                self.rails.store.append(event)
                return self.rails.store.get(event_id)
            self.rails.append_event(event)
            return event
