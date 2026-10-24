"""The chaser's daily run: every open invoice against its vendor's agreement rules.

Code decides every step; nothing here calls a model or sends a message.

    1. Find the vendor's rules (SpacetimeDB `agreement_rule`, written by the agreement agent).
       No agreement -> skip. Agreement waiting for the controller -> hold, do not chase.
    2. Deadline = invoice received + `pay_within_days` (calendar or business days).
    3. Bank shows the invoice paid in full -> hand off to the anomaly agent.
    4. Not past the deadline -> wait.
    5. Overdue -> next reminder on the approved ladder (polite -> firm -> escalation), at least
       five days apart, drafted from a controller-approved template.
    6. After the escalation reminder -> escalate to the owner and the management company.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from rails.guards import render
from rails.guards.policy import policy_check
from rails.guards.templates import LEVEL_ORDER

_SUFFIX = re.compile(r"\b(llc|inc|co|corp|ltd|company|l\.l\.c\.)\b\.?", re.IGNORECASE)


def norm_name(name: str) -> str:
    return " ".join(_SUFFIX.sub("", name or "").lower().replace(",", " ").split())


def add_days(start: date, days: int, basis: str) -> date:
    if basis != "business":
        return start + timedelta(days=days)
    d, left = start, days
    while left:
        d += timedelta(days=1)
        if d.weekday() < 5:
            left -= 1
    return d


@dataclass(frozen=True)
class Decision:
    invoice_id: str
    vendor_name: str
    action: str                    # skip | hold | not_due | handoff_anomaly | reminder | escalate | wait
    reason: str
    level: str | None = None
    due_date: str | None = None
    days_overdue: int = 0
    amount_owed: float = 0.0
    message: str | None = None
    rule_id: str | None = None
    rule_quote: str | None = None

    @property
    def recorded(self) -> bool:
        """Actions worth keeping in the log (waiting days are recomputed, not stored)."""
        return self.action in ("reminder", "escalate", "handoff_anomaly", "hold")

    def action_id(self) -> str:
        tail = self.level or self.action
        return f"{self.invoice_id}:{self.action}:{tail}"

    def to_dict(self) -> dict:
        return asdict(self)


def _rules_by_vendor(rule_rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rule_rows:
        out.setdefault(norm_name(r["vendor_name"]), []).append(
            {**r, "params": json.loads(r["params"]) if isinstance(r["params"], str) else r["params"]})
    return out


def decide(line, vendor_name: str, manager_name: str, rules: list[dict], history: list[dict],
           today: date) -> Decision:
    base = {"invoice_id": line.invoice_id, "vendor_name": vendor_name}
    if not rules:
        return Decision(**base, action="skip", reason="no saved agreement for this vendor")
    if any(r["status"] in ("blocking", "pending_controller") for r in rules):
        return Decision(**base, action="hold",
                        reason="agreement is waiting for controller confirmation; not chasing")
    if any(r["status"] == "rejected" for r in rules):
        return Decision(**base, action="skip", reason="agreement was rejected by the controller")
    term = next((r for r in rules if r["rule_type"] == "pay_within_days"
                 and r["status"] == "active"), None)
    if term is None:
        return Decision(**base, action="skip", reason="agreement has no payment deadline rule")
    if line.invoice_received is None:
        return Decision(**base, action="skip", reason="invoice receipt date unknown")
    p = term["params"]
    due = add_days(line.invoice_received, int(p["days"]), p.get("basis", "calendar"))
    cite = {"rule_id": term["rule_id"], "rule_quote": term.get("source_quote"),
            "due_date": due.isoformat()}
    owed = round(line.amount - line.paid_amount, 2)
    if owed <= 0:
        return Decision(**base, **cite, action="handoff_anomaly",
                        reason=f"bank shows ${line.paid_amount:,.2f} paid on {line.paid_on}; "
                               "anomaly agent checks amount, recipient and timing")
    if today <= due:
        return Decision(**base, **cite, action="not_due", amount_owed=owed,
                        reason=f"due {due} ({(due - today).days} days left) under "
                               f"'{term['check']}'")
    overdue = (today - due).days
    sent = sorted((h for h in history if h["action"] == "reminder"), key=lambda h: h["as_of"])
    levels = [h["level"] for h in sent]
    if levels and levels[-1] == LEVEL_ORDER[-1]:
        if any(h["action"] == "escalate" for h in history):
            return Decision(**base, **cite, action="wait", days_overdue=overdue, amount_owed=owed,
                            reason="already escalated to the owner and management")
        return Decision(**base, **cite, action="escalate", days_overdue=overdue, amount_owed=owed,
                        reason=f"{overdue} days past the {due} deadline after all three "
                               "reminders; owner and management notified")
    level = LEVEL_ORDER[LEVEL_ORDER.index(levels[-1]) + 1] if levels else LEVEL_ORDER[0]
    last = date.fromisoformat(sent[-1]["as_of"]) if sent else None
    ok, why = policy_check("send_chase", "chaser", {
        "template_id": f"tpl-{level}", "prior_levels": levels,
        "days_since_last": (today - last).days if last else None})
    if not ok:
        return Decision(**base, **cite, action="wait", days_overdue=overdue, amount_owed=owed,
                        reason=f"next reminder not due yet ({why})")
    body = render(f"tpl-{level}", manager_name=manager_name, amount=f"${owed:,.2f}",
                  vendor_name=vendor_name, invoice_ref=line.invoice_id)
    return Decision(**base, **cite, action="reminder", level=level, days_overdue=overdue,
                    amount_owed=owed, message=body,
                    reason=f"{overdue} days past the {due} deadline; {level} reminder drafted")


def run(rails, rule_rows: list[dict], history_rows: list[dict], today: date,
        manager_id: str = "keyway") -> list[Decision]:
    from workflow.names import names_from
    name = names_from(rails)
    rules = _rules_by_vendor(rule_rows)
    history: dict[str, list[dict]] = {}
    for h in history_rows:
        history.setdefault(h["invoice_id"], []).append(h)
    out = []
    for line in rails.ledger(manager_id):
        if line.duplicate_of:
            continue
        vendor = name(line.vendor_id)
        out.append(decide(line, vendor, name(manager_id), rules.get(norm_name(vendor), []),
                          history.get(line.invoice_id, []), today))
    return out
