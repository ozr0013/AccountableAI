"""Activity feed and counters, derived only from the event log (nothing is stored twice).

Each entry says who did what and why. "system" entries are automatic; "controller" entries are
the human decisions; "input" entries are facts that arrived (judge console, clock). Reminders
are labelled as issued and recorded: nothing is emailed.
"""

from __future__ import annotations

from datetime import datetime

from contracts.models import Event

# workflow outputs that count as an automatic action
AUTOMATIC = {"alert_raised", "alert_rejected", "alert_withdrawn", "chase_sent", "chase_held",
             "case_proposed", "bank_change_held", "reply_read"}
INPUTS = {"fact_corrected", "chase_reply", "bank_change_requested", "clock_advanced"}


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _label_refreshes(events: list[Event]) -> set[str]:
    """Ids of alert_raised events that only refresh an alert that was already open (its figures,
    such as days past due, changed). They are updates, not new alerts."""
    open_ids: set[str] = set()
    refreshes: set[str] = set()
    for e in events:
        if e.type == "alert_raised":
            aid = e.payload["draft"]["id"]
            if aid in open_ids:
                refreshes.add(e.id)
            open_ids.add(aid)
        elif e.type == "alert_withdrawn":
            open_ids.discard(e.payload["alert_id"])
    return refreshes


def entry(e: Event, refresh: bool = False) -> dict | None:
    p = e.payload
    base = {"id": e.id, "at": e.learned_at.isoformat(), "type": e.type,
            "manager_id": e.manager_id, "invoice_id": e.invoice_id}

    def out(actor, text, **extra):
        return {**base, "actor": actor, "text": text, **extra}

    if e.type == "alert_raised":
        d, v = p["draft"], p["verdict"]
        word = "Alert updated (figures changed)" if refresh else "Alert raised"
        return out("system", f"{word}: {d['headline']}", kind=d["kind"], amount=d["amount"],
                   verdict=v["status"], refresh=refresh,
                   citations=[c["source_id"] for cl in d["claims"] for c in cl["citations"]])
    if e.type == "alert_rejected":
        d, v = p["draft"], p["verdict"]
        why = (v["reasons"] or ["no reason given"])[0]
        return out("system", f"Draft alert not raised ({v['status']} by the verifier): "
                             f"{d['headline']}. {why}", kind=d["kind"], verdict=v["status"])
    if e.type == "alert_withdrawn":
        return out("system", f"Alert withdrawn: {p['reason']}", alert_id=p["alert_id"])
    if e.type in ("chase_sent", "chase_held"):
        c = p["chase"]
        ids = ", ".join(c["invoice_ids"])
        if e.type == "chase_sent":
            return out("system", f"{c['level'].capitalize()} reminder issued to {c['manager_id']} "
                                 f"for {ids} (approved template {c['template_id']}; recorded, "
                                 "not emailed)", level=c["level"])
        return out("system", f"Reminder held by policy: {p.get('policy', '')}", level=c["level"])
    if e.type == "case_proposed":
        return out("system", p["recommendation"], approval_id=p.get("approval_id"),
                   proposed_hold=p["case"]["proposed_hold"])
    if e.type == "bank_change_held":
        return out("system", f"Bank-detail change from {p['vendor_id']}: {_money(p['held_amount'])} "
                             f"held pending identity check and controller approval ({p['policy']})",
                   amount=p["held_amount"])
    if e.type == "reply_read":
        return out("system", f"Reply read: {p['verdict']}")
    if e.type == "approval_requested":
        c = p["case"]
        return out("system", f"Waiting for the controller: hold {_money(c['proposed_hold'])} "
                             f"(approval {p['approval_id']})", approval_id=p["approval_id"])
    if e.type == "approval_decided":
        verb = "approved" if p["approved"] else "declined"
        c = p["case"]
        return out("controller", f"{p['approver']} {verb} the hold of {_money(c['proposed_hold'])}",
                   approval_id=p["approval_id"], approved=p["approved"])
    if e.type == "clock_advanced":
        return out("input", f"Time moved to {p['as_of']} ({p.get('reason', 'demo clock')})")
    if e.type == "fact_corrected":
        return out("input", f"Fact received: {p['fact_key']} is {p['value']}")
    if e.type == "chase_reply":
        return out("input", f"Reply from {e.manager_id}: \"{p.get('text', '')}\"")
    if e.type == "bank_change_requested":
        return out("input", f"Vendor {p.get('vendor_id')} asked to change bank details")
    if e.type == "document_ingested" and e.id.startswith("ev-judge-"):
        return out("input", f"Document received: {p.get('text', '')}")
    return None


def feed(events: list[Event], limit: int = 100, since: datetime | None = None) -> list[dict]:
    refreshes = _label_refreshes(events)
    items = [x for e in events if (x := entry(e, e.id in refreshes))]
    if since:
        items = [i for i in items if i["at"] > since.isoformat()]
    return items[-limit:][::-1]            # newest first


def stats(events: list[Event]) -> dict:
    """Automatic actions vs human decisions. Facts that arrive are inputs, not decisions."""
    refreshes = _label_refreshes(events)
    auto = [e for e in events if e.type in AUTOMATIC and e.id not in refreshes]
    human = [e for e in events if e.type == "approval_decided"]
    inputs = [e for e in events if e.type in INPUTS
              or (e.type == "document_ingested" and e.id.startswith("ev-judge-"))]
    by = {}
    for e in auto:
        by[e.type] = by.get(e.type, 0) + 1
    invoices = len({e.payload["data"]["invoice_id"] for e in events
                    if e.type == "document_ingested" and e.payload.get("kind") == "vendor_invoice"
                    and e.payload.get("data", {}).get("invoice_id")})
    return {"automatic_actions": len(auto), "automatic_by_type": by,
            "alerts_updated": len(refreshes),
            "human_decisions": len(human), "inputs_received": len(inputs), "invoices": invoices,
            "human_decisions_per_100_invoices": round(100 * len(human) / invoices, 2)
            if invoices else 0.0}
