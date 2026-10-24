"""Policy gate: `policy_check(action, actor, payload) -> (allowed, reason)`.

Fails closed. Actions outside the vocabulary below are refused until someone adds them here, so
a new capability can never slip in unreviewed. There is no money-moving function anywhere;
`move_money` and friends exist only to be refused.

payload keys used (all optional unless stated):
  actor_role        "agent" | "controller" (default: controller iff actor starts with "controller")
  proposed_by       who proposed the thing being approved / sent
  template_id, prior_levels, days_since_last              (send_chase)
  template_id, body, to, registered_to                   (send_statement_notice)
  body, to, approval {"draft_digest", "approved_by"}      (send_outbound)
  citations, text                                         (create_alert)
  identity_verified, approved_by, requested_by            (update_bank_details)
"""

from __future__ import annotations

import hashlib

from .templates import APPROVED_TEMPLATES, LEVEL_ORDER, MIN_DAYS_BETWEEN, STATEMENT_NOTICE_TEMPLATE
from .wording import banned_terms_in

NEVER = {"move_money", "pay", "pay_vendor", "transfer", "wire", "release_funds", "disburse",
         "initiate_payment", "send_money", "send_unapproved"}
READ_ONLY = {"read", "draft", "draft_chase", "draft_document_request", "propose_hold",
             "recheck", "withdraw_alert", "resolve_alert", "decline", "request_bank_change",
             "build_case", "record_reply"}
KNOWN = READ_ONLY | NEVER | {"approve", "hold", "send_chase", "send_outbound", "create_alert",
                             "update_bank_details", "send_statement_notice"}


def draft_digest(to: str, body: str) -> str:
    """What the controller approves: this exact recipient and text."""
    return hashlib.sha256(f"{to}\n{body}".encode()).hexdigest()


def _controller(actor: str, payload: dict) -> bool:
    """Role comes from payload["actor_role"]; without it, only an actor named "controller..."
    counts. Agents ("case_agent", "chaser", ...) never do."""
    role = payload.get("actor_role")
    if role is not None:
        return role == "controller"
    return actor.lower().startswith("controller")


def policy_check(action: str, actor: str, payload: dict) -> tuple[bool, str]:
    p = payload or {}
    if action in NEVER:
        return False, f"{action} is never allowed: no agent or service can move money"
    if action not in KNOWN:
        return False, f"unknown action {action!r}; add it to rails/guards/policy.py after review"

    if action == "approve":
        if p.get("proposed_by") == actor:
            return False, "proposer cannot approve"
        if not _controller(actor, p):
            return False, "only the controller can approve"
        return True, "ok"

    if action == "hold":
        if not _controller(actor, p):
            return False, "agents recommend holds; only the controller places one"
        return True, "ok"

    if action == "send_chase":
        t = APPROVED_TEMPLATES.get(p.get("template_id", ""))
        if t is None:
            return False, "reminders may only use a controller-approved template"
        prior = list(p.get("prior_levels", []))
        i = LEVEL_ORDER.index(t["level"])
        if prior and prior[-1] == t["level"] and i < len(LEVEL_ORDER) - 1 or t["level"] in prior[:-1]:
            return False, "reminder level already sent"
        if i > 0 and (not prior or prior[-1] != LEVEL_ORDER[i - 1]):
            return False, f"off schedule: send the {LEVEL_ORDER[i - 1]} reminder first"
        if prior and (p.get("days_since_last") is None or p["days_since_last"] < MIN_DAYS_BETWEEN):
            return False, f"off schedule: wait {MIN_DAYS_BETWEEN} days between reminders"
        return True, "ok"

    if action == "send_outbound":
        ap = p.get("approval") or {}
        if not p.get("body") or not p.get("to"):
            return False, "outbound needs a recipient and a body"
        if ap.get("draft_digest") != draft_digest(p["to"], p["body"]):
            return False, "outbound needs approval of this exact draft"
        if not ap.get("approved_by") or ap["approved_by"] == p.get("proposed_by"):
            return False, "proposer cannot approve their own draft"
        if bad := banned_terms_in(p["body"]):
            return False, f"accusatory wording blocked: {', '.join(bad)}"
        return True, "ok"

    if action == "send_statement_notice":
        # The anomaly detector's note: a code-rendered template, to the middleman on file only.
        if p.get("template_id") != STATEMENT_NOTICE_TEMPLATE:
            return False, "a statement notice must use the approved notice template"
        if not p.get("to") or p.get("to") != p.get("registered_to"):
            return False, "a statement notice goes only to the middleman's registered address"
        if bad := banned_terms_in(p.get("body", "")):
            return False, f"accusatory wording blocked: {', '.join(bad)}"
        return True, "ok"

    if action == "create_alert":
        if not p.get("citations"):
            return False, "no alert without a cited record"
        if bad := banned_terms_in(p.get("text", "")):
            return False, f"accusatory wording blocked: {', '.join(bad)}"
        return True, "ok"

    if action == "update_bank_details":
        if not p.get("identity_verified"):
            return False, "vendor bank-detail change held: identity not verified"
        if not p.get("approved_by") or p["approved_by"] == p.get("requested_by"):
            return False, "vendor bank-detail change needs controller approval from someone else"
        return True, "ok"

    if action == "request_bank_change":
        return True, "held for identity verification and controller approval"

    return True, "ok"
