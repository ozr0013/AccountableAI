"""Accounting gate: the books must agree.

  * balanced        funded = paid + held + owed per manager, unless the gap is exactly the money
                    funded on flagged duplicate invoices (reported separately, never absorbed)
  * closed months   no new money record (funding / bank / confirmation) dated inside a month that
                    a `period_closed` event already closed; corrections go through fact_corrected
  * replay          the same content ingested twice under different ids is a replay duplicate
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from contracts.models import Event

from ..recon import breakdown

MONEY_KINDS = {"funding", "manager_bank", "vendor_confirmation"}


@dataclass
class Issue:
    check: str            # balanced | closed_month | replay_duplicate
    detail: str
    ref: str = ""


def accounting_check(events: list[Event]) -> list[Issue]:
    issues: list[Issue] = []

    managers = sorted({e.manager_id for e in events if e.manager_id})
    for m in managers:
        lines = breakdown(events, m)
        gap = sum(l.diff for l in lines) - sum(l.duplicate_funded for l in lines)
        if gap:
            issues.append(Issue("balanced", f"{m}: ${gap / 100:,.2f} of funded money is unaccounted for", m))

    closed: dict[str, Event] = {}
    for e in sorted(events, key=lambda e: e.learned_at):
        if e.type == "period_closed":
            closed[e.payload["month"]] = e
        elif e.type == "document_ingested" and e.payload.get("kind") in MONEY_KINDS:
            month = e.valid_from.strftime("%Y-%m")
            c = closed.get(month)
            if c and e.learned_at > c.learned_at:
                issues.append(Issue("closed_month", f"{e.id} posts into closed month {month}", e.id))

    seen: dict[str, str] = {}
    for e in events:
        if e.type not in ("document_ingested", "chase_reply"):
            continue
        body = {k: v for k, v in e.payload.items() if k != "id"}
        h = hashlib.sha256(json.dumps([e.type, body], sort_keys=True, default=str).encode()).hexdigest()
        if h in seen and seen[h] != e.id:
            issues.append(Issue("replay_duplicate", f"{e.id} repeats the content of {seen[h]}", e.id))
        seen.setdefault(h, e.id)
    return issues
