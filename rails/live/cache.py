"""Reuse answers to identical questions, so moving the clock only costs what is new.

A time jump re-assesses every open item, but for most of them nothing a model sees has changed.
The cause agent and the verifier get the same documents, the same terms and the same claims, so
they would give the same answer, minutes later. This remembers the answer, keyed by a hash of
exactly what the model was shown (plus which model it was), in Postgres so it survives a restart.

Not remembered, on purpose: rules-only answers (already instant), a verifier that failed closed
(a transient outage must not stick), and anything about a different model. A changed fact
changes the key, so it always gets a fresh answer. Reuse is counted and shown in /stats.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading

from contracts.models import AgreementTerms, AlertDraft, CauseFinding, Document, LedgerLine, PaymentTrail, Verdict

SCHEMA = """
CREATE TABLE IF NOT EXISTS answer_cache (
    key        TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    payload    JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""
_DIGITS = re.compile(r"\d+")


def _h(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


class AnswerCache:
    def __init__(self, conn):
        self.conn = conn
        conn.execute(SCHEMA)
        self.hits = self.misses = 0
        self._lock = threading.Lock()

    def get(self, kind: str, key: str) -> dict | None:
        row = self.conn.execute("SELECT payload FROM answer_cache WHERE key = %s AND kind = %s",
                                (key, kind)).fetchone()
        with self._lock:
            if row:
                self.hits += 1
            else:
                self.misses += 1
        return row[0] if row else None

    def put(self, kind: str, key: str, payload: dict) -> None:
        from psycopg.types.json import Jsonb
        self.conn.execute("INSERT INTO answer_cache (key, kind, payload) VALUES (%s,%s,%s) "
                          "ON CONFLICT (key) DO NOTHING", (key, kind, Jsonb(payload)))

    def counts(self) -> dict:
        return {"answers_reused": self.hits, "answers_computed": self.misses}


class CachedAgents:
    """Wraps the judgment agents; the model-backed trail and cause calls are remembered."""

    def __init__(self, inner, cache: AnswerCache):
        self._inner, self._cache = inner, cache
        self.llm = getattr(inner, "llm", None) or getattr(getattr(inner, "cause", None), "llm", None)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def build_trail(self, line: LedgerLine, docs: list[Document]) -> PaymentTrail:
        """The trail agent's text varies from call to call, and the cause agent is keyed on it,
        so this is remembered first: it is what makes every later question repeat exactly."""
        if not self.llm:
            return self._inner.build_trail(line, docs)
        shown = sorted((d.id, hashlib.sha256((d.text + json.dumps(d.data, sort_keys=True, default=str)).encode()).hexdigest())
                       for d in docs)
        key = _h("trail", os.getenv("AGENT_MODEL"), line.model_dump(mode="json"), shown)
        if hit := self._cache.get("trail", key):
            return PaymentTrail.model_validate(hit)
        out = self._inner.build_trail(line, docs)
        self._cache.put("trail", key, out.model_dump(mode="json"))
        return out

    def classify_cause(self, line: LedgerLine, trail: PaymentTrail, terms: AgreementTerms) -> CauseFinding:
        if not self.llm:                                   # rules are instant; nothing to reuse
            return self._inner.classify_cause(line, trail, terms)
        key = _h("cause", os.getenv("AGENT_MODEL"), line.model_dump(mode="json"),
                 trail.model_dump(mode="json"), terms.model_dump(mode="json"))
        if hit := self._cache.get("cause", key):
            return CauseFinding.model_validate(hit)
        out = self._inner.classify_cause(line, trail, terms)
        self._cache.put("cause", key, out.model_dump(mode="json"))
        return out


class CachedVerifier:
    """Wraps the verifier; a real model verdict is remembered, a fail-closed one never is."""

    def __init__(self, inner, cache: AnswerCache):
        self._inner, self._cache = inner, cache

    def __getattr__(self, name):
        return getattr(self._inner, name)

    @staticmethod
    def _key(draft: AlertDraft, evidence: list[Document]) -> str:
        d = draft.model_dump(mode="json")
        d["headline"] = _DIGITS.sub("#", d["headline"])    # "37 days past due" vs "38": same alert
        docs = sorted((e.id, hashlib.sha256((e.text + json.dumps(e.data, sort_keys=True, default=str)).encode()).hexdigest())
                      for e in evidence)
        return _h("verdict", os.getenv("VERIFIER_MODEL"), d, docs)

    def verify(self, draft: AlertDraft, evidence: list[Document]) -> Verdict:
        key = self._key(draft, evidence)
        if hit := self._cache.get("verdict", key):
            return Verdict.model_validate(hit).model_copy(update={"alert_id": draft.id})
        v = self._inner.verify(draft, evidence)
        failed_closed = any("failing closed" in r for r in v.reasons)
        if v.meta.get("model") and not failed_closed:
            self._cache.put("verdict", key, v.model_dump(mode="json"))
        return v
