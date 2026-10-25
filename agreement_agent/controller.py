"""Controller sign-off for an agreement the agent found incomplete.

The agent never confirms its own review. When a required term is missing or unclear, the
agreement waits as `needs_controller` and its rules stay pending or blocking. A controller then:

    confirm  supplies every missing required term (recorded as the controller's statement, with
             their identity as the source) -> rules are rebuilt and go active
    reject   the agreement is not relied on; its rules are marked rejected
"""
from __future__ import annotations

import json

from .models import FieldResult, Review
from .rules_from_terms import REQUIRED, rules_for

MAX_VALUE = 500


class ControllerError(ValueError):
    """The decision can't be applied (wrong state, missing or unexpected terms)."""


def review_from_row(row: dict) -> Review:
    """Rebuild the stored review from its SpacetimeDB `agreement` row."""
    return Review(doc_id=row["doc_id"], filename=row["filename"], source="upload",
                  kind=row["kind"], kind_reason="", complete=row["complete"],
                  fields=[FieldResult.model_validate(f) for f in json.loads(row["fields"])],
                  steps=json.loads(row["steps"]), mode=row["mode"], cost_usd=row["cost_usd"])


def confirm(row: dict, values: dict[str, str], controller: str, note: str | None
            ) -> tuple[Review, list[dict]]:
    if row["status"] != "needs_controller":
        raise ControllerError(f"agreement is {row['status']}; only needs_controller can be confirmed")
    review = review_from_row(row)
    open_terms = {f.name for f in review.fields if f.name in REQUIRED and f.status != "found"}
    unexpected = set(values) - open_terms
    if unexpected:
        raise ControllerError(f"only missing required terms can be supplied: {sorted(open_terms)}")
    if open_terms - set(values):
        raise ControllerError(f"supply every missing required term: {sorted(open_terms - set(values))}")
    for name, value in values.items():
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_VALUE:
            raise ControllerError(f"{name} must be 1 to {MAX_VALUE} characters")
    where = f"controller:{controller}"
    fields = [f.model_copy(update={"status": "found", "value": values[f.name].strip(),
                                   "quote": values[f.name].strip(), "location": where,
                                   "by": "controller",
                                   "note": note or "Supplied by the controller."})
              if f.name in values else f for f in review.fields]
    confirmed = review.model_copy(update={
        "fields": fields, "complete": True, "escalation": None,
        "steps": [*review.steps, f"controller {controller} supplied: {', '.join(sorted(values))}"]})
    rules = rules_for(confirmed)
    if any(r["status"] == "blocking" for r in rules):
        raise ControllerError("a supplied term could not be turned into a checkable rule")
    return confirmed, rules


def reject(row: dict, rules: list[dict], controller: str) -> tuple[Review, list[dict]]:
    if row["status"] not in ("needs_controller", "active", "confirmed"):
        raise ControllerError(f"agreement is already {row['status']}")
    review = review_from_row(row)
    review = review.model_copy(update={"steps": [*review.steps, f"rejected by {controller}"]})
    return review, [{**r, "status": "rejected"} for r in rules]
