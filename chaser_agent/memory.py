"""What the agents remember between runs.

Each agent run is short and starts with an empty conversation; its memory lives in SpacetimeDB
and is read back into the next run's context, a bounded amount at a time:

    previous runs  what this agent decided on the same subject before, why, and what triggered it
                   (read from agent_task, which already keeps every run's outcome and trace)
    lessons        patterns an agent recorded about a vendor or the manager, each with the
                   documents that prove it (table agent_lesson)

Memory never overrides the books: a lesson is a lead, the documents are the evidence. Every
lesson cites documents that exist, the controller can delete any of them, and a demo reset
clears the demo's lessons.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date

RUNS_RECALLED = 5
SWEEPS_RECALLED = 3
LESSONS_RECALLED = 8


def book_of(rails) -> str:
    return getattr(rails.store, "book", None) or "public"


def previous_runs(mirror, agent: str, subject: str, today: date, n: int = RUNS_RECALLED) -> list[dict]:
    """This agent's last runs on the same invoice (or 'portfolio' for the sweep), oldest first."""
    from .pipeline import tasks
    done = [t for t in tasks(mirror, invoice_id=subject, agent=agent)
            if t["as_of"] < today.isoformat() and t["status"] in ("done", "failed")]
    out = []
    for t in done[-n:]:
        o = t["outcome"] or {}
        said = o.get("summary") or o.get("rationale") or o.get("error") or ""
        out.append({"day": t["as_of"], "decision": o.get("decision"), "verdict": o.get("verdict"),
                    "decided_by": o.get("decided_by") or "you (Claude)", "trigger": t["created_by"],
                    "said": said[:500]})
    return out


def lessons(mirror, book: str, subjects: list[str] | None = None, n: int = LESSONS_RECALLED) -> list[dict]:
    """Lessons for these subjects (all subjects when None), newest last."""
    rows = mirror.query(f"SELECT * FROM agent_lesson WHERE book = '{book.replace(chr(39), '')}'")
    wanted = {s.lower() for s in subjects} if subjects else None
    keep = [r for r in rows if wanted is None or r["subject"].lower() in wanted]
    keep.sort(key=lambda r: (r["as_of"], r["key"]))
    return [{"subject": r["subject"], "lesson": r["lesson"], "evidence": json.loads(r["evidence"] or "[]"),
             "learned_by": r["by_agent"], "on": r["as_of"], "id": r["key"]} for r in keep[-n:]]


def open_flags(mirror) -> list[dict]:
    """Flags still waiting for the controller, so a sweep does not chase what it already raised."""
    from . import api
    return [{"invoice_id": h["invoice_id"], "kind": h["level"], "raised": h["as_of"],
             "said": (h.get("message") or "")[:240]}
            for h in api.history(mirror) if h["action"] == "flag" and h["status"] == "needs_controller"]


def subjects_known(rails, manager_id: str = "keyway") -> set[str]:
    from workflow.names import names_from
    name = names_from(rails)
    vendors = {e.payload.get("name") for e in rails.events()
               if e.type == "entity_registered" and e.payload.get("kind") == "vendor"}
    return {v for v in vendors if v} | {name(manager_id)}


def match_subject(given: str, known: set[str]) -> str | None:
    """'Keyway' or 'Rio Grande Plumbing LLC' finds 'Keyway Property Management' / 'Rio Grande
    Plumbing': exact, then the same name without a company suffix, then a unique prefix."""
    from .daily import norm_name
    g = norm_name(given)
    by_norm = {norm_name(k): k for k in known}
    if g in by_norm:
        return by_norm[g]
    hits = [k for n, k in by_norm.items() if g and (n.startswith(g) or g.startswith(n))]
    return hits[0] if len(hits) == 1 else None


def remember(mirror, rails, agent: str, subject: str, lesson: str, evidence_ids: list[str],
             today: date) -> dict:
    """Record a lesson about a vendor or the manager, citing documents in the books."""
    from . import books
    from .runtime import emit
    found = match_subject(subject, subjects_known(rails))
    if found is None:
        return {"error": f"subject must be a vendor or the manager by name; known: {sorted(subjects_known(rails))}"}
    subject = found
    missing = sorted(set(evidence_ids) - books.known(rails, "documents", "document_id", evidence_ids))
    if not evidence_ids or missing:
        return {"error": f"cite document ids that exist in the books; unknown: {missing or 'none given'}"}
    if not 20 <= len(lesson) <= 600:
        return {"error": "a lesson is one or two sentences (20-600 characters)"}
    book = book_of(rails)
    key = f"{book}|{subject}|{hashlib.sha256(lesson.encode()).hexdigest()[:12]}"
    mirror.call("remember_lesson", [key, book, subject, lesson, json.dumps(evidence_ids), agent,
                                    today.isoformat()])
    emit({"type": "lesson_saved", "subject": subject, "lesson": lesson[:300], "evidence": evidence_ids})
    return {"remembered": key, "subject": subject}


def tool_for(mirror, rails, agent: str, today: date):
    from .runtime import Tool
    return Tool(
        "remember_lesson",
        "Record a pattern for later runs about a vendor or the manager (e.g. 'Keyway claimed "
        "payment without proof on INV-A and INV-B'), citing the documents that prove it. Not a "
        "decision. Only for patterns worth knowing next time, not for this invoice's facts.",
        {"type": "object", "additionalProperties": False, "required": ["subject", "lesson", "evidence_ids"],
         "properties": {"subject": {"type": "string", "description": "a vendor's name or the manager's name"},
                        "lesson": {"type": "string", "minLength": 20, "maxLength": 600},
                        "evidence_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1}}},
        lambda subject, lesson, evidence_ids: remember(mirror, rails, agent, subject, lesson, evidence_ids, today))
