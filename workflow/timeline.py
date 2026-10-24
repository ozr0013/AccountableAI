"""The investigation, day by day: timeline(rails, days=15).

Reads only the event log. For each day in the window:
  facts      what arrived (documents, corrected facts, manager replies, clock moves, funding asks)
  decisions  DecisionRecords: each agent's reasoning steps, mode (rules / Claude / fallback)
  outcomes   what the workflow did (alerts raised / refreshed / withdrawn, reminders, cases)
  human      what a person decided (approvals, funding instructions, confirmed terms)
  unexplained_end  vendor money still owed with no explanation at the end of the day
"""

from __future__ import annotations

from datetime import date, timedelta

from contracts.models import AlertKind, Event

_FACT_TYPES = {"document_ingested", "fact_corrected", "chase_reply", "bank_change_requested",
               "funding_review_requested", "terms_confirmed"}
_HUMAN_TYPES = {"approval_decided", "funding_instruction", "bank_change_decided"}


def _fact(e: Event) -> dict:
    p = e.payload
    if e.type == "document_ingested":
        text = (p.get("text") or "").replace("\n", " ")
        what = f"{p.get('kind', 'document').replace('_', ' ')}: {text[:160]}"
        return {"id": e.id, "type": p.get("kind"), "what": what, "source_id": p.get("id"),
                "invoice_id": e.invoice_id}
    if e.type == "fact_corrected":
        what = f"Corrected {p.get('fact_key')} → {p.get('value')}"
    elif e.type == "chase_reply":
        what = f"{e.manager_id} replied: “{p.get('text', '')}”"
    elif e.type == "funding_review_requested":
        what = f"Oakline is about to fund {e.manager_id} ${float(p.get('amount', 0)):,.2f}"
    elif e.type == "terms_confirmed":
        what = f"Agreement terms confirmed for {e.manager_id}"
    elif e.type == "world_meta":
        what = f"Demo clock moved to {p.get('as_of')}"
    else:
        what = e.type.replace("_", " ")
    return {"id": e.id, "type": e.type, "what": what, "source_id": e.id, "invoice_id": e.invoice_id}


def _outcome(e: Event, seen: set[str]) -> dict | None:
    p = e.payload
    if e.type == "alert_raised":
        d = p["draft"]
        new = d["id"] not in seen
        seen.add(d["id"])
        return {"type": "alert_raised" if new else "alert_refreshed", "alert_id": d["id"],
                "invoice_ids": d["invoice_ids"], "what": d["headline"], "kind": d["kind"],
                "amount": d["amount"]}
    if e.type == "alert_withdrawn":
        return {"type": "alert_withdrawn", "alert_id": p["alert_id"],
                "invoice_ids": [p.get("invoice_id")], "what": p.get("reason")}
    if e.type == "alert_rejected":
        return {"type": "alert_blocked", "alert_id": p["draft"]["id"],
                "invoice_ids": p["draft"]["invoice_ids"],
                "what": "; ".join(p["verdict"]["reasons"])}
    if e.type in ("chase_sent", "chase_held"):
        c = p["chase"]
        return {"type": e.type, "invoice_ids": c["invoice_ids"],
                "what": f"{c['level']} reminder {'sent' if e.type == 'chase_sent' else 'held: ' + str(p.get('policy'))}"}
    if e.type == "reply_read":
        return {"type": "reply_read", "invoice_ids": p["reply"].get("invoice_ids", []),
                "what": p.get("verdict")}
    if e.type == "case_proposed":
        return {"type": "case_proposed", "invoice_ids": [], "what": p.get("recommendation"),
                "hold": p["case"]["proposed_hold"]}
    if e.type == "bank_change_held":
        return {"type": "bank_change_held", "invoice_ids": [p.get("invoice_id")],
                "what": f"{p.get('vendor_id')} bank-detail change held: {p.get('policy')}"}
    return None


def _human(e: Event) -> dict:
    p = e.payload
    if e.type == "funding_instruction":
        what = (f"{p.get('by')} {'approved' if p.get('approved') else 'declined'} the hold: "
                f"release ${p.get('release', 0):,.2f}, hold ${p.get('hold', 0):,.2f}")
    elif e.type == "approval_decided":
        what = f"{p.get('approver')} {'approved' if p.get('approved') else 'declined'} {p.get('approval_id')}"
    else:
        what = f"{p.get('by')}: bank-detail change {'allowed' if p.get('allowed') else 'kept held'}"
    return {"type": e.type, "what": what}


def timeline(rails, days: int = 15, as_of: date | None = None) -> dict:
    events = sorted(rails.events(), key=lambda e: (e.learned_at, e.id))
    end = as_of or rails.now()
    start = end - timedelta(days=days - 1)
    cards = {start + timedelta(days=i): {"facts": [], "decisions": [], "outcomes": [], "human": []}
             for i in range(days)}

    seen_alerts: set[str] = set()
    open_unexplained: dict[str, float] = {}
    balance: dict[date, float] = {}

    def owed() -> float:
        return round(sum(open_unexplained.values()), 2)

    for e in events:
        day = e.learned_at.date()
        if e.type == "alert_raised" and e.payload["draft"]["kind"] == AlertKind.unexplained_unpaid:
            open_unexplained[e.payload["draft"]["id"]] = float(e.payload["draft"]["amount"])
        elif e.type == "alert_withdrawn":
            open_unexplained.pop(e.payload["alert_id"], None)
        out = _outcome(e, seen_alerts) if e.type in (
            "alert_raised", "alert_withdrawn", "alert_rejected", "chase_sent", "chase_held",
            "reply_read", "case_proposed", "bank_change_held") else None
        balance[day] = owed()
        card = cards.get(day)
        if card is None:
            continue
        if e.type in _FACT_TYPES or (e.type == "world_meta" and e.id.startswith("ev-world-clock")):
            card["facts"].append(_fact(e))
        elif e.type == "agent_decision":
            card["decisions"].append(e.payload)
        elif out is not None:
            card["outcomes"].append(out)
        elif e.type in _HUMAN_TYPES:
            card["human"].append(_human(e))

    before = max((d for d in balance if d < start), default=None)
    running = balance[before] if before else 0.0
    out_days = []
    for i, (day, c) in enumerate(sorted(cards.items()), start=1):
        prev, running = running, balance.get(day, running)
        modes = [d.get("mode") for d in c["decisions"]]
        out_days.append({
            "date": day.isoformat(), "label": f"Day {i:02d}", **c,
            "unexplained_start": prev, "unexplained_end": running,
            "quiet": not any(c[k] for k in ("facts", "decisions", "outcomes", "human")),
            "counts": {"facts": len(c["facts"]), "decisions": len(c["decisions"]),
                       "claude": modes.count("llm"), "fallback": modes.count("fallback"),
                       "outcomes": len(c["outcomes"]), "human": len(c["human"])},
        })
    return {"as_of": end.isoformat(), "start": start.isoformat(), "days": out_days}
