"""The chaser's daily run: every open invoice against its vendor's agreement rules.

Code decides every step; nothing here calls a model or sends a message.

    1. Find the vendor's rules (SpacetimeDB `agreement_rule`, written by the agreement agent).
       No agreement -> skip. Agreement waiting for the controller -> hold, do not chase.
    2. Deadline = invoice received + `pay_within_days` (calendar or business days).
    3. The bank shows any payment for the invoice -> hand off to the anomaly agent, which checks
       the amount (including partial payments), recipient and timing.
    4. Not past the deadline -> wait.
    5. Overdue -> next reminder on the approved ladder (polite -> firm -> escalation). Spacing
       comes from the agreement's reminder or grace clause (`reminder_cadence`), never below the
       approved 5-day floor. Levels in CHASER_AUTO_LEVELS (default: all three) are queued
       automatically; any other level waits for controller approval. A declined draft is
       redrafted after one cadence. Only proof of payment (a bank record) stops the ladder.
    6. One cadence after the escalation reminder -> escalate: notify the owner and the
       management company, propose holding the amount owed from the next funding, and open a
       controller review. The notice goes out automatically (CHASER_AUTO_ESCALATE=on); the hold
       is always the controller's decision.

A vendor without its own agreement is held to the deadline terms of the owner's management
agreement with the manager (`rules_for`).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta

from rails.guards import render
from rails.guards.policy import policy_check
from rails.guards.templates import LEVEL_ORDER, MIN_DAYS_BETWEEN

_SUFFIX = re.compile(r"\b(llc|inc|co|corp|ltd|company|l\.l\.c\.)\b\.?", re.IGNORECASE)
# Reminder levels sent without approval (the templates themselves are controller-approved).
AUTO_LEVELS = {x.strip() for x in os.getenv("CHASER_AUTO_LEVELS", "polite,firm,escalation").split(",")
               if x.strip()}
AUTO_ESCALATE = os.getenv("CHASER_AUTO_ESCALATE", "on").lower() == "on"
# What a management agreement contributes for vendors without their own: when to pay, how often
# to remind. Its payee and account rules describe paying the manager, not the vendor.
MANAGEMENT_TERMS = ("pay_within_days", "reminder_cadence")
# Keyway pays "using funds provided by Owner": when Oakline wires late, Keyway gets this many days
# from the wire before the invoice counts as overdue.
FUNDING_GRACE_DAYS = int(os.getenv("CHASER_FUNDING_GRACE_DAYS", "3"))
# How many days in a row Claude may choose to wait while the schedule says to act.
MAX_MODEL_WAITS = int(os.getenv("CHASER_MAX_MODEL_WAITS", "2"))
DELIVERED = {"queued", "approved"}


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
    action: str          # skip | hold | not_due | handoff_anomaly | reminder | escalate | wait
    reason: str
    level: str | None = None
    due_date: str | None = None
    days_overdue: int = 0
    amount_owed: float = 0.0
    message: str | None = None
    rule_id: str | None = None
    rule_quote: str | None = None
    status: str = "recorded"   # queued | needs_approval | needs_controller | recorded
    attempt: int = 0

    @property
    def recorded(self) -> bool:
        """Actions worth keeping in the log (waiting days are recomputed, not stored)."""
        return self.action in ("reminder", "escalate", "handoff_anomaly", "hold")

    def action_id(self) -> str:
        return f"{self.invoice_id}:{self.action}:{self.level or self.action}:{self.attempt}"

    def to_dict(self) -> dict:
        return {**asdict(self), "action_id": self.action_id()}


def _rules_by_vendor(rule_rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rule_rows:
        out.setdefault(norm_name(r["vendor_name"]), []).append(
            {**r, "params": json.loads(r["params"]) if isinstance(r["params"], str) else r["params"]})
    return out


def rules_for(by_vendor: dict[str, list[dict]], vendor: str, manager_name: str) -> list[dict]:
    """The vendor's own agreement rules; without them, the deadline and reminder terms of the
    management agreement the manager signed with the owner (it says when every vendor is paid)."""
    own = by_vendor.get(norm_name(vendor), [])
    if own:
        return own
    return [r for r in by_vendor.get(norm_name(manager_name), []) if r["rule_type"] in MANAGEMENT_TERMS]


def _cadence(rules: list[dict]) -> tuple[int, str]:
    rule = next((r for r in rules if r["rule_type"] == "reminder_cadence"
                 and r["status"] == "active"), None)
    if rule is None:
        return MIN_DAYS_BETWEEN, f"approved default of {MIN_DAYS_BETWEEN} days"
    days = int(rule["params"]["days"])
    if days < MIN_DAYS_BETWEEN:
        return MIN_DAYS_BETWEEN, f"agreement says {days} days; approved floor is {MIN_DAYS_BETWEEN}"
    return days, f"agreement: {rule.get('source_quote') or rule['check']}"


def funded_deadline(due: date, line) -> date:
    """The agreement deadline, moved out when Oakline's own funding arrived too late for it."""
    if line.funded_on and line.funded_on + timedelta(days=FUNDING_GRACE_DAYS) > due:
        return line.funded_on + timedelta(days=FUNDING_GRACE_DAYS)
    return due


def bank_rows_by_invoice(rails, manager_id: str = "keyway", today: date | None = None) -> dict[str, list[dict]]:
    """Every bank payment row filed so far, by invoice: what Keyway's bank says it paid, to whom."""
    out: dict[str, list[dict]] = {}
    for d in rails.documents(manager_id):
        if d.kind.value != "manager_bank" or (today and d.dated and d.dated > today):
            continue
        for p in d.data.get("payments", []):
            if p.get("invoice_id"):
                out.setdefault(p["invoice_id"], []).append({**p, "document": d.id})
    return out


def verified_line(line, bank_rows: list[dict]):
    """The ledger line with only payments to the invoice's own vendor counted. A payment to a
    different vendor does not pay this invoice (the ledger counts any row with its number).
    Returns (line, source): source is 'bank', 'vendor confirmation' or None."""
    if not bank_rows:
        return line, ("vendor confirmation" if line.paid_amount > 0 else None)
    right = [r for r in bank_rows if r.get("vendor_id") in (None, line.vendor_id)]
    paid = round(sum(float(r.get("amount") or 0) for r in right), 2)
    paid_on = max((date.fromisoformat(r["on"]) for r in right if r.get("on")), default=None)
    return line.model_copy(update={"paid_amount": paid, "paid_on": paid_on if paid else None}), "bank"


def decide(line, vendor_name: str, manager_name: str, rules: list[dict], history: list[dict],
           today: date, invoice=None, payment_seen: bool | None = None,
           paid_source: str | None = None) -> Decision:
    """`payment_seen`: some bank row names this invoice (even one paying another vendor).
    `paid_source`: where the counted payment comes from, for the reasons Claude reads."""
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
    start = line.invoice_received
    if p.get('basis', 'calendar') not in ('calendar', 'business'):
        return Decision(**base, action='hold', reason='Unsupported agreement day basis; controller review required.')
    if p.get('from') == 'invoice date':
        raw = invoice.data.get('invoice_date') or invoice.dated if invoice else None
        try:
            start = raw if isinstance(raw, date) else date.fromisoformat(raw)
        except (ValueError, TypeError):
            return Decision(**base, action='hold', reason='Invoice date evidence is missing; controller review required.')
    if p.get('undisputed_only') and invoice and (invoice.data.get('disputed') or invoice.data.get('status') == 'disputed'):
        return Decision(**base, action='hold', reason='Disputed invoice requires controller review before chasing.')
    for rule in rules:
        if (rule['rule_type'] == 'amount' and rule['status'] == 'active'
                and rule['params'].get('per') == 'fixed'
                and round(float(rule['params']['amount']) * 100) != round(line.amount * 100)):
            return Decision(**base, action='hold', reason='Invoice amount differs from the agreement; controller review required.')
    agreed = add_days(start, int(p["days"]), p.get("basis", "calendar"))
    due = funded_deadline(agreed, line)
    cite = {"rule_id": term["rule_id"], "rule_quote": term.get("source_quote"),
            "due_date": due.isoformat()}
    owed = round(line.amount - line.paid_amount, 2)
    seen = line.paid_amount > 0 if payment_seen is None else (payment_seen or line.paid_amount > 0)
    handed = any(h["action"] == "handoff_anomaly" for h in history)
    if seen and not handed:
        source = paid_source or "bank"
        if line.paid_amount <= 0:
            what = "a bank payment names this invoice but went to a different vendor"
        else:
            what = (f"{source} shows ${line.paid_amount:,.2f} paid "
                    + ("in full" if owed <= 0 else f"of ${line.amount:,.2f} (${owed:,.2f} still owed)")
                    + f" on {line.paid_on}")
        return Decision(**base, **cite, action="handoff_anomaly", amount_owed=max(owed, 0),
                        reason=f"{what}; anomaly agent checks amount, recipient and timing")
    if seen and owed <= 0:
        return Decision(**base, **cite, action="skip", reason="paid in full; anomaly review done")
    # Unpaid, or a balance still owed after a short or misdirected payment: keep chasing it.
    late_wire = (f"; Oakline funded on {line.funded_on}, after the agreement date {agreed}, so "
                 f"Keyway has {FUNDING_GRACE_DAYS} days from the wire") if due != agreed else ""
    if today <= due:
        return Decision(**base, **cite, action="not_due", amount_owed=owed,
                        reason=f"due {due} ({(due - today).days} days left) under "
                               f"'{term['check']}'{late_wire}")
    if not line.funded_amount:
        return Decision(**base, **cite, action="skip",
                        reason="Oakline has not funded this invoice; nothing to chase")
    return _chase(base, cite, line, manager_name, rules, history, today, due, owed)


def _chase(base, cite, line, manager_name, rules, history, today, due, owed) -> Decision:
    overdue = (today - due).days
    every, why_every = _cadence(rules)
    common = {**base, **cite, "days_overdue": overdue, "amount_owed": owed}
    drafts = sorted((h for h in history if h["action"] == "reminder"),
                    key=lambda h: (h["as_of"], h["action_id"]))
    if any(h['status'] == 'awaiting_delivery' for h in drafts):
        return Decision(**common, action='wait', reason='Reminder queued; waiting for confirmed delivery before another reminder.')
    if any(h["status"] == "needs_approval" for h in drafts):
        return Decision(**common, action="wait", reason="reminder draft awaiting controller approval")
    sent = [h for h in drafts if h["status"] in DELIVERED]
    since = (today - date.fromisoformat(sent[-1]["as_of"])).days if sent else None
    if sent and sent[-1]["level"] == LEVEL_ORDER[-1]:
        if any(h["action"] == "escalate" for h in history):
            return Decision(**common, action="wait",
                            reason="escalated; waiting for payment or the controller's decision")
        if since < every:
            return Decision(**common, action="wait",
                            reason=f"escalation reminder sent {since} days ago; escalating "
                                   f"after {every} ({why_every})")
        notice = (f"{line.invoice_id} ({base['vendor_name']}): ${owed:,.2f} is {overdue} days past "
                  f"the {due} deadline after all three reminders. Notified: Oakline (owner) and "
                  f"{manager_name} (management). Proposed: hold ${owed:,.2f} from the next funding "
                  f"to {manager_name} until it is paid. Controller decision required.")
        return Decision(**common, action="escalate", message=notice,
                        status="queued" if AUTO_ESCALATE else "needs_controller",
                        reason="escalated to owner and management; hold proposed for the controller")
    level = LEVEL_ORDER[LEVEL_ORDER.index(sent[-1]["level"]) + 1] if sent else LEVEL_ORDER[0]
    declined = [h for h in drafts if h["status"] == "rejected"]
    if declined and declined[-1]["level"] == level:
        ago = (today - date.fromisoformat(declined[-1]["as_of"])).days
        if ago < every:
            return Decision(**common, action="wait",
                            reason=f"controller declined the {level} reminder {ago} days ago; "
                                   f"redrafting after {every} days")
    if since is not None and since < every:
        return Decision(**common, action="wait",
                        reason=f"last reminder {since} days ago; next after {every} ({why_every})")
    ok, why = policy_check("send_chase", "chaser", {
        "template_id": f"tpl-{level}", "prior_levels": [h["level"] for h in sent],
        "days_since_last": since})
    if not ok:
        return Decision(**common, action="wait", reason=f"next reminder not allowed yet ({why})")
    body = render(f"tpl-{level}", manager_name=manager_name, amount=f"${owed:,.2f}",
                  vendor_name=base["vendor_name"], invoice_ref=line.invoice_id)
    auto = level in AUTO_LEVELS
    return Decision(**common, action="reminder", level=level, message=body,
                    status="queued" if auto else "needs_approval",
                    attempt=sum(h["level"] == level for h in drafts),
                    reason=f"{overdue} days past the {due} deadline; {level} reminder "
                           + ("queued automatically" if auto else "drafted for controller approval"))


def run(rails, rule_rows: list[dict], history_rows: list[dict], today: date,
        manager_id: str = "keyway") -> list[Decision]:
    from workflow.names import names_from
    name = names_from(rails)
    rules = _rules_by_vendor(rule_rows)
    history: dict[str, list[dict]] = {}
    for h in history_rows:
        history.setdefault(h["invoice_id"], []).append(h)
    from .patterns import describe, vendor_patterns
    patterns = {p["vendor"]: p for p in vendor_patterns(rails, rule_rows, today, manager_id)}
    out = []
    invoice_docs = {d.data.get('invoice_id'): d for d in rails.documents(manager_id) if d.kind == 'vendor_invoice'}
    bank = bank_rows_by_invoice(rails, manager_id, today)
    for ledger_line in rails.ledger(manager_id):
        if ledger_line.duplicate_of:
            continue
        rows = bank.get(ledger_line.invoice_id, [])
        line, source = verified_line(ledger_line, rows)
        vendor = name(line.vendor_id)
        d = decide(line, vendor, name(manager_id), rules_for(rules, vendor, name(manager_id)),
                   history.get(line.invoice_id, []), today, invoice_docs.get(line.invoice_id),
                   payment_seen=bool(rows), paid_source=source)
        if d.action in ("reminder", "escalate", "wait") and vendor in patterns:
            # The vendor's payment history travels with every chasing decision.
            d = replace(d, reason=f"{d.reason}; history: {describe(patterns[vendor])}")
        out.append(d)
    return out
