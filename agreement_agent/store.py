"""Where reviews and owner escalations are kept: JSON files under agreement_agent/out/ (gitignored).

out/reviews/<doc_id>.json      every review, newest overwrites
out/escalations/<id>.json      drafted notices for the owner (status "drafted": nothing is sent)
"""

from __future__ import annotations

from pathlib import Path

from .models import Escalation, Review

OUT = Path(__file__).resolve().parent / "out"


def save(review: Review, out: Path = OUT) -> Path:
    (out / "reviews").mkdir(parents=True, exist_ok=True)
    path = out / "reviews" / f"{review.doc_id}.json"
    path.write_text(review.model_dump_json(indent=2))
    if review.escalation:
        (out / "escalations").mkdir(parents=True, exist_ok=True)
        (out / "escalations" / f"{review.escalation.id}.json").write_text(review.escalation.model_dump_json(indent=2))
    return path


def escalations(out: Path = OUT) -> list[Escalation]:
    d = out / "escalations"
    return sorted((Escalation.model_validate_json(p.read_text()) for p in d.glob("*.json")),
                  key=lambda e: e.created_at, reverse=True) if d.exists() else []
