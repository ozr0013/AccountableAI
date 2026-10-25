"""Judge Console on the real agents: a file goes into the books and wakes the agents that matter.

    reader     the same reader the console always used (agents/extract): PDF, .eml, bank CSV
    event log  each document is filed in the demo books (SpacetimeDB, book demo_live)
    checks     the automatic payment and statement checks run on the books as they now stand
    agents     the anomaly agent reviews each invoice the checks flag; the chaser takes a reply
               or a payment for the invoice it names, with the document as its note
    email      notes the checks queued for Keyway (a statement line that disagrees with what
               Oakline approved) go out, and each send is recorded

Every step is emitted on the agents' live stream (/pipeline/stream), so the console builds its
cards from what actually ran. Nothing here decides anything: the code checks, the agents decide.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from . import demo, pipeline
from .runtime import emit

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "dashboard" / "feed" / "samples"
MAX_BYTES = 2_000_000
_world = None
_state: dict = {"busy": False, "last": None, "runs": 0}


def samples() -> list[dict]:
    return json.loads((SAMPLES / "manifest.json").read_text())


def sample_path(name: str) -> Path:
    if name not in {s["file"] for s in samples()}:
        raise KeyError(name)
    return SAMPLES / name


def _vendors():
    global _world
    if _world is None:
        from world import generate
        from world.generator import ALL_SCENARIOS
        _world = generate(13, ALL_SCENARIOS, 100)
    return _world.vendors


def read(path: Path) -> tuple[list, dict]:
    """The console's reader: documents, plus what it read for the Reader card."""
    from agents.extract import parse_file, to_document
    from agents.extract.fields import VendorIndex
    from agents.extract.link import link
    try:
        from world.mess.noise import VENDOR_DOMAINS
    except ImportError:
        VENDOR_DOMAINS = {}
    records = link(parse_file(path, f"inbox/{path.name}", VendorIndex(_vendors(), dict(VENDOR_DOMAINS))))
    flags = [w for p in records for w in p.warnings]
    usable = [p for p in records if p.kind is not None and p.dated]
    invoices = sorted({i for p in usable for i in ([p.data.get("invoice_id")]
                       + [ln.get("invoice_id") for ln in p.data.get("lines", [])]) if i})
    reader = {"file": path.name, "kinds": sorted({p.kind.value for p in usable}), "invoices": invoices,
              "flags": flags[:6]}
    return [to_document(p) for p in usable], reader


def books_figures(rails, mirror, today) -> dict:
    """The console's four figures, from the agents' own view of the books."""
    from . import api
    rows = pipeline.ledger_view(rails, mirror, today)
    paid = round(sum(r["paid_amount"] or 0 for r in rows), 2)
    left = round(sum(max(r["funded_amount"] - (r["paid_amount"] or 0), 0) for r in rows), 2)
    late = round(sum(max(r["funded_amount"] - (r["paid_amount"] or 0), 0) for r in rows
                     if r["state"] in ("overdue", "partial", "misdirected")), 2)
    waiting = sum(1 for h in api.history(mirror) if h["status"] in ("needs_controller", "needs_approval"))
    return {"paid": paid, "left": left, "late": late, "attention": waiting}


def run_file(path: Path, label: str | None = None) -> dict:
    """File one document in the demo books and let the agents work it. Blocking; run it off the
    request thread and follow the live stream."""
    from rails.events.ingest import document_event

    from . import api, connected
    from .demo import _statement_reviews
    mirror, rails = api._mirror(), demo.rails()
    today = rails.now()
    _state["runs"] += 1
    emit({"type": "console_started", "label": label or path.name, "file": path.name, "day": today.isoformat()})
    before = books_figures(rails, mirror, today)

    docs, reader = read(path)
    emit({"type": "reader", **reader})
    if not docs:
        out = {"ok": False, "error": f"The reader found nothing usable in {path.name}", "reader": reader}
        emit({"type": "console_done", **out})
        return out

    stamp = datetime.combine(today, time(15, 0), tzinfo=UTC) + timedelta(minutes=_state["runs"] % 50)
    filed, arrived, already = [], [], []
    known = {e.id for e in rails.events()}
    for doc in docs:
        ev = document_event(doc, stamp)
        if ev.id in known:
            already.append(doc.id)
            continue
        rails.append_event(ev)
        filed.append(doc.id)
        arrived.append({"id": doc.id, "kind": doc.kind.value, "lines": doc.data.get("lines")})
    emit({"type": "filed", "documents": filed, "already": already, "book": "SpacetimeDB · demo_live"})

    # The automatic checks on the books as they now stand.
    cases = {c["invoice_id"]: c for c in connected.reconcile(rails, mirror, pipeline.MANAGER, as_of=today)}
    touched = reader["invoices"]
    findings = {i: sorted(f["code"] for f in cases[i]["findings"]) for i in touched if i in cases}
    notices = [i for i in touched if any(o["kind"] == "statement_notice" and o["status"] == "queued"
                                         for o in cases.get(i, {}).get("outbox", []))]
    emit({"type": "checks", "findings": findings, "notices": notices})

    # Wake the agents: the anomaly agent for flagged statement lines and payments, the chaser
    # for a reply or a payment on an invoice it is chasing.
    queued = _statement_reviews(rails, mirror, today, arrived)
    for a in arrived:
        if a["kind"] in ("manager_reply", "manager_bank", "vendor_confirmation", "vendor_notice"):
            for inv in touched:
                queued.append(pipeline.enqueue(
                    mirror, "chaser", inv, f"New {a['kind'].replace('_', ' ')} for this invoice: {a['id']}",
                    {"note": a["id"]}, today, "console", key=f"console{_state['runs']}"))
    emit({"type": "agents_woken", "tasks": queued})
    worked = pipeline.process(rails, mirror, today, limit=12)

    cases_now = connected.reconcile(rails, mirror, pipeline.MANAGER, as_of=today)
    api.mark_handoffs_reviewed(mirror, cases_now)
    delivered = demo._deliver(rails, mirror, today)

    after = books_figures(rails, mirror, today)
    out = {"ok": True, "label": label or path.name, "reader": reader, "filed": filed, "already": already,
           "findings": findings, "notices": notices, "worked": worked, "emails": len(delivered),
           "books": {"before": before, "after": after}, "day": today.isoformat()}
    emit({"type": "console_done", **{k: v for k, v in out.items() if k != "worked"},
          "decisions": [{k: w.get(k) for k in ("agent", "invoice_id", "decision", "verdict", "summary", "rationale")}
                        for w in worked]})
    return out
