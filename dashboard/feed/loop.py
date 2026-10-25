"""The three-agent loop, one agent at a time, for the dashboard's Live loop page.

    1. agreement  an agreement arrives -> the agreement agent checks it -> stored, or the owner is told
    2. chaser     the due date has passed, no proof of payment -> reminders -> escalation, owner told
    3. anomaly    the middleman's statement shows a different amount -> owner alerted, middleman note

Each step runs the real agents on the session's rails (rules mode unless the session is live) and
returns one plain card: what came in, what the agent checked, what it decided, who was told.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from contracts.models import AlertKind, Document, Event, SourceKind

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "agreement_agent" / "samples"
AGREEMENTS = [SAMPLES / "loop" / "rio_grande_repair_agreement.pdf",       # complete
              SAMPLES / "greenedge_landscaping_agreement.pdf"]            # due date + payment missing
INVOICE = "INV-KW-0301"           # Rio Grande Plumbing, $1,240: the agreement above, unpaid in the world
STATEMENT_SHOWS = 620.0           # Keyway's statement: half of what the owner approved


def _source(path: Path):
    from agreement_agent.connectors import LocalFolder
    return next(LocalFolder(path).fetch())


_LABELS: dict[str, str] = {}


def _step_kind(text: str) -> str:
    if text.startswith(("checked", "escalated")):
        return "result"
    if text.startswith("marked"):
        return "guard"
    if text.startswith("recorded"):
        return "evidence"
    return "fact"


def _readable(text: str) -> str:
    """'looked for payment_due: 1 candidate(s)' -> 'looked for Payment due date or terms: 1 candidate(s)'."""
    if not _LABELS:
        from agreement_agent import fields as F
        _LABELS.update({sp.name: sp.label for sp in F.INVOICE + F.AGREEMENT})   # agreement labels win
    for name, label in _LABELS.items():
        text = text.replace(f" {name}:", f" {label}:")
    return text


def agreement_step(s) -> dict:
    """Both agreements through the agreement agent. A complete one is recorded in the event log
    (the system's database); an incomplete one produces the owner's notice. Its reasoning goes
    to the live stream like every other agent's."""
    import os

    from agreement_agent.graph import review
    use_model = s.live and bool(os.getenv("ANTHROPIC_API_KEY"))
    items, cost = [], 0.0
    for path in AGREEMENTS:
        question = f"Does {path.name} have everything the owner needs to pay the vendor?"
        s.hub.publish({"type": "agent_started", "agent": "agreement_check", "invoice_id": None,
                       "question": question, "trigger": None})
        r = review(_source(path), use_model=use_model)
        cost += r.cost_usd
        s.hub.publish({"type": "decision", "id": f"agreement-{r.doc_id}", "trigger": None, "seq": 0,
                       "at": None, "day": s.rails.now().isoformat(), "agent": "agreement_check",
                       "invoice_id": None, "question": question,
                       "steps": [{"kind": _step_kind(t), "text": _readable(t), "citations": []}
                                 for t in r.steps],
                       "answer": {"complete": r.complete}, "confidence": None,
                       "mode": "llm" if r.mode == "model" else "rules", "citations": [],
                       "model": None, "tokens_in": None, "tokens_out": None, "ms": 0})
        found = {f.name: f.value for f in r.fields if f.status == "found"}
        record = None
        if r.complete:
            record = {"agreement": path.stem.replace("_", " ").title(),
                      "owner": found.get("owner_name"), "vendor": found.get("vendor_name"),
                      "amount": found.get("payment_amount"), "due": found.get("payment_due"),
                      "payment_info": found.get("payment_info")}
            at = s._clock(9, 0)
            s.rails.append_event(Event(
                id=f"ev-agreement-recorded-{r.doc_id}-{s.actions}", type="agreement_recorded",
                manager_id="keyway", valid_from=at, learned_at=at, payload=record,
                source={"source_id": r.doc_id, "kind": SourceKind.agreement}))
        items.append({
            "file": r.filename, "complete": r.complete,
            "fields": [{"label": f.label, "status": f.status, "value": f.value, "where": f.location,
                        "note": f.note} for f in r.fields],
            "stored": record,
            "owner_notice": ({"subject": r.escalation.subject, "body": r.escalation.body}
                             if r.escalation else None)})
    return {"step": "agreement", "agent": "Agreement agent", "mode": "model" if use_model else "rules",
            "items": items, "cost": round(cost, 4)}


def _ladder(s) -> list[dict]:
    from workflow.book import WorkflowBook
    book = WorkflowBook.load(s.rails)
    return [{"level": c.level, "sent": c.sent_at.date().isoformat() if c.sent_at else None,
             "invoices": c.invoice_ids, "body": c.body}
            for c in book.chases.get("keyway", []) if INVOICE in c.invoice_ids and c.sent_at]


def chaser_step(s, days: int = 5) -> dict:
    """The due date has passed and there is no proof of payment: show the reminders so far, move
    the clock on, and show what the chaser does next."""
    from agents.chaser.rules import proof_docs
    line = next(l for l in s.rails.ledger("keyway") if l.invoice_id == INVOICE)
    names = s.snapshot()["meta"]["names"]
    today = s.rails.now()
    proof = proof_docs([INVOICE], s.rails.documents("keyway", INVOICE))
    before = _ladder(s)
    res = s.advance(days)
    after = _ladder(s)
    new = after[len(before):]
    latest = (after or before or [None])[-1]
    return {"step": "chaser", "agent": "Chaser agent",
            "invoice": INVOICE, "vendor": names.get(line.vendor_id, line.vendor_id),
            "amount": line.amount, "funded": line.funded_amount, "paid": line.paid_amount,
            "due": line.due_date.isoformat() if line.due_date else None,
            "today": today.isoformat(), "days_late": (today - line.due_date).days if line.due_date else 0,
            "proof": [c.source_id for c in proof],
            "earlier": before, "new": new, "moved_to": s.rails.now().isoformat(), "days": days,
            "escalated": bool(latest and latest["level"] == "escalation"),
            "owner_notified": bool(latest and latest["level"] == "escalation"),
            "latest": latest, "emails": res.get("emails", []), "cost": res.get("cost", 0.0),
            "mode": "model" if s.live else "rules"}


def anomaly_step(s) -> dict:
    """Keyway sends a statement that shows less than the owner approved for the invoice."""
    from workflow.book import WorkflowBook
    line = next(l for l in s.rails.ledger("keyway") if l.invoice_id == INVOICE)
    names = s.snapshot()["meta"]["names"]
    vendor = names.get(line.vendor_id, line.vendor_id)
    on: date = s.rails.now()
    sid = f"doc-stmt-keyway-{on.isoformat()}-{s.actions}"
    stmt = Document(
        id=sid, kind=SourceKind.manager_statement, manager_id="keyway", dated=on,
        text=f"Keyway Property Management statement {on:%B %Y}. L1: {on:%m/%d} {vendor} {INVOICE} "
             f"${STATEMENT_SHOWS:,.2f} PAID.",
        data={"lines": [{"line": "L1", "invoice_id": INVOICE, "amount": STATEMENT_SHOWS,
                         "status": "paid", "on": on.isoformat()}]})
    at = s._clock(10, 0)
    ev = Event(id=f"ev-{sid}", type="document_ingested", manager_id="keyway", valid_from=at,
               learned_at=at, payload=stmt.model_dump(mode="json"),
               source={"source_id": sid, "kind": SourceKind.manager_statement})
    before = s.snapshot()
    res = s._act("statement", f"Keyway sent a statement: {INVOICE} {vendor} ${STATEMENT_SHOWS:,.2f} paid.",
                 [ev], [INVOICE], before)
    book = WorkflowBook.load(s.rails)
    alert = next((a for a in book.open_alerts("keyway")
                  if a.draft.kind == AlertKind.statement_mismatch and a.draft.invoice_ids == [INVOICE]), None)
    owner, middleman = None, None
    if alert:
        owner_part, _, note = (alert.draft.recommended_action or "").partition("Note to the middleman: ")
        owner = {"headline": alert.draft.headline, "detail": alert.draft.claims[0].text,
                 "action": owner_part.strip(), "verified": alert.verdict.status.value}
        middleman = {"body": note.strip()}
    return {"step": "anomaly", "agent": "Anomaly detector", "invoice": INVOICE, "vendor": vendor,
            "statement": {"id": sid, "text": stmt.text}, "approved": line.amount,
            "stated": STATEMENT_SHOWS, "difference": round(STATEMENT_SHOWS - line.amount, 2),
            "caught": alert is not None, "owner": owner, "middleman": middleman,
            "emails": res.get("emails", []), "cost": res.get("cost", 0.0),
            "mode": "model" if s.live else "rules"}
