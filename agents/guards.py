"""Checks every agent runs on an LLM answer before it is used. Code, never a model."""

from __future__ import annotations

import re
from collections.abc import Iterable

from contracts.models import Citation, Document
from rails.guards import banned_terms_in


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def citation_problems(cites: Iterable[Citation], docs: Iterable[Document]) -> list[str]:
    """Each citation must name a given document; a quote must appear in it verbatim
    (the verifier rejects claims whose quote isn't in the source)."""
    by_id = {d.id: d for d in docs}
    out = []
    for c in cites:
        d = by_id.get(c.source_id)
        if d is None:
            out.append(f"cites unknown document {c.source_id!r}")
        elif c.quote and _norm(c.quote) not in _norm(d.text):
            out.append(f"quote not found verbatim in {c.source_id}: {c.quote!r}")
    return out


def wording_problems(*texts: str) -> list[str]:
    bad = sorted({t for text in texts for t in banned_terms_in(text)})
    return [f"accusatory wording not allowed: {', '.join(bad)}"] if bad else []


def render_docs(docs: Iterable[Document]) -> str:
    """Documents as tagged blocks with their ids, oldest first, for the prompt."""
    out = []
    for d in sorted(docs, key=lambda d: (str(d.dated or ""), d.id)):
        out.append(f'<document id="{d.id}" kind="{d.kind.value}" dated="{d.dated or ""}">\n'
                   f"{d.text}\n</document>")
    return "\n".join(out)
