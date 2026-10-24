"""Payment-trail agent (task 3): one timeline per invoice; conflicts settled by source-trust rules.

The LLM reads every document's text and writes the timeline and a plain explanation. The
winner is NOT the LLM's call: it comes from rails' source-trust rules (bank > vendor > manager),
and the guards reject a timeline that cites unknown documents or drops a deciding source.
"""

from __future__ import annotations

import json
from datetime import date

from pydantic import BaseModel, Field

from contracts.models import Citation, Document, LedgerLine, PaymentTrail, TrailEntry

from ..guards import citation_problems, render_docs, wording_problems
from ..llm import StructuredLLM, guarded_call
from . import rules
from .prompts import SYSTEM


class _Entry(BaseModel):
    source_id: str = Field(description="exact id of the document this entry comes from")
    on: str | None = Field(description="YYYY-MM-DD the event happened, if known")
    claim: str = Field(description="what this source says about the payment, in a few words")
    locator: str | None = Field(default=None, description="line or clause, e.g. L14")


class TrailDraft(BaseModel):
    entries: list[_Entry]
    reasoning: str = Field(description="2-3 sentences: what happened and why the winner wins")


def _date(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


class TrailAgent:
    def __init__(self, llm: StructuredLLM | None = None):
        self.llm = llm

    def build_trail(self, line: LedgerLine, docs: list[Document]) -> PaymentTrail:
        base = rules.build_trail(line, docs)
        by_id = {d.id: d for d in docs}
        must_cover = {c.source_id for c in base.winning_sources} | {
            e.citation.source_id for e in base.entries}

        def check(out: TrailDraft) -> list[str]:
            cites = [Citation(source_id=e.source_id, kind=by_id[e.source_id].kind)
                     for e in out.entries if e.source_id in by_id]
            problems = [f"cites unknown document {e.source_id!r}"
                        for e in out.entries if e.source_id not in by_id]
            problems += citation_problems(cites, docs)
            problems += [f"timeline leaves out {s}" for s in sorted(must_cover)
                         if s not in {e.source_id for e in out.entries}]
            problems += [f"bad date {e.on!r}" for e in out.entries if e.on and not _date(e.on)]
            return problems + wording_problems(out.reasoning, *(e.claim for e in out.entries))

        out, _ = guarded_call(self.llm, TrailDraft, SYSTEM, self._prompt(line, docs, base), check)
        if out is None:
            return base
        entries = [TrailEntry(on=_date(e.on), kind=by_id[e.source_id].kind, claim=e.claim,
                              citation=Citation(source_id=e.source_id, kind=by_id[e.source_id].kind,
                                                locator=e.locator))
                   for e in out.entries]
        return base.model_copy(update={
            "entries": sorted(entries, key=lambda e: (str(e.on or ""), e.citation.source_id)),
            "reasoning": out.reasoning})

    @staticmethod
    def _prompt(line: LedgerLine, docs: list[Document], base: PaymentTrail) -> str:
        facts = {
            "invoice": line.model_dump(mode="json"),
            "trust_rules_verdict": {
                "winning_claim": base.winning_claim, "conflict": base.conflict,
                "winning_sources": [c.source_id for c in base.winning_sources],
                "reason": base.reasoning},
            "documents_that_must_appear": sorted({e.citation.source_id for e in base.entries}),
        }
        return (f"<facts>\n{json.dumps(facts, indent=1, default=str)}\n</facts>\n\n"
                f"<documents>\n{render_docs(docs)}\n</documents>\n\n"
                f"Build the payment timeline for {line.invoice_id}.")
