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
                       + [ln.get("invoice_id") for ln in p.data.get("lines", [])]
                       + [pm.get("invoice_id") for pm in p.data.get("payments", [])]) if i})
    reader = {"file": path.name, "kinds": sorted({p.kind.value for p in usable}), "invoices": invoices,
              "flags": flags[:6]}
    return [to_document(p) for p in usable], reader


def _paid(rails) -> dict[str, float]:
    from .daily import bank_rows_by_invoice, verified_line
    bank = bank_rows_by_invoice(rails, pipeline.MANAGER)
    return {l.invoice_id: verified_line(l, bank.get(l.invoice_id, []))[0].paid_amount
            for l in rails.ledger(pipeline.MANAGER) if not l.duplicate_of}


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
    from .demo import PAYMENT_FINDINGS, STATEMENT_FINDINGS
    mirror, rails = api._mirror(), demo.rails()
    today = rails.now()
    _state["runs"] += 1
    n_tasks = len(pipeline.tasks(mirror))
    emit({"type": "console_started", "label": label or path.name, "file": path.name, "day": today.isoformat()})
    before = books_figures(rails, mirror, today)

    docs, reader = read(path)
    emit({"type": "reader", **reader})
    if not docs:
        out = {"ok": False, "error": f"The reader found nothing usable in {path.name}", "reader": reader}
        emit({"type": "console_done", **out})
        return out

    paid_before = _paid(rails)
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

    # The automatic checks on the books as they now stand. An invoice counts as touched when the
    # document names it, or when filing it changed what the books say was paid (a bank row
    # without an invoice number is matched by vendor and amount).
    paid_after = _paid(rails)
    moved = sorted(i for i in paid_after if paid_after[i] != paid_before.get(i))
    touched = sorted(set(reader["invoices"]) | set(moved))
    if moved:
        emit({"type": "matched", "invoices": moved,
              "paid": {i: [paid_before.get(i, 0.0), paid_after[i]] for i in moved}})
    cases = {c["invoice_id"]: c for c in connected.reconcile(rails, mirror, pipeline.MANAGER, as_of=today)}
    findings = {i: sorted(f["code"] for f in cases[i]["findings"]) for i in touched if i in cases}
    notices = [i for i in touched if any(o["kind"] == "statement_notice" and o["status"] == "queued"
                                         for o in cases.get(i, {}).get("outbox", []))]
    emit({"type": "checks", "findings": findings, "notices": notices})

    # Wake the agents: the anomaly agent for flagged statement lines and payments, the chaser
    # for a reply or a payment on an invoice it is chasing.
    key = f"console{_state['runs']}"
    queued = []
    statement = any(a["kind"] == "manager_statement" for a in arrived)
    for inv in touched:
        codes = set(findings.get(inv, []))
        doc = ", ".join(a["id"] for a in arrived)
        if statement and codes & (STATEMENT_FINDINGS | PAYMENT_FINDINGS):
            queued.append(pipeline.enqueue(
                mirror, "anomaly", inv, "Keyway's new statement covers this invoice and the checks found: "
                + ", ".join(sorted(codes)).replace("_", " "), {}, today, "console", key=key))
        elif not statement:
            what = ", ".join(sorted({a["kind"].replace("_", " ") for a in arrived}))
            why = "the books now show a payment for it" if inv in moved else "it names this invoice"
            queued.append(pipeline.enqueue(
                mirror, "chaser", inv, f"New {what} ({doc}); {why}.", {"note": doc}, today, "console", key=key))
    emit({"type": "agents_woken", "tasks": queued})
    worked = pipeline.process(rails, mirror, today, limit=12)

    cases_now = connected.reconcile(rails, mirror, pipeline.MANAGER, as_of=today)
    api.mark_handoffs_reviewed(mirror, cases_now)
    delivered = demo._deliver(rails, mirror, today)

    after = books_figures(rails, mirror, today)
    out = {"ok": True, "label": label or path.name, "reader": reader, "filed": filed, "already": already,
           "findings": findings, "notices": notices, "worked": worked, "emails": len(delivered),
           "books": {"before": before, "after": after}, "day": today.isoformat(),
           "usage": _usage_since(n_tasks, mirror)}
    emit({"type": "console_done", **{k: v for k, v in out.items() if k != "worked"},
          "decisions": [{k: w.get(k) for k in ("agent", "invoice_id", "decision", "verdict", "summary", "rationale")}
                        for w in worked]})
    return out


# -- the other three inputs ------------------------------------------------------------------
def _usage_since(n_tasks_before: int, mirror) -> dict:
    """Claude calls and cost of the tasks this run created (the page shows what it spent)."""
    from .pipeline import tasks
    PRICE = {"input": 4e-6, "cached": 4e-7, "output": 2e-5}         # Claude Opus 5.5, per token
    calls = inp = cached = out = 0
    for t in tasks(mirror)[n_tasks_before:]:
        u = (t.get("trace") or {}).get("usage") or {}
        if u.get("output_tokens"):
            calls += 1
            inp += u.get("input_tokens") or 0
            cached += u.get("cache_read_input_tokens") or 0
            out += u.get("output_tokens") or 0
    return {"calls": calls, "cost": round(inp * PRICE["input"] + cached * PRICE["cached"] + out * PRICE["output"], 3)}


def write_email(sender: str, subject: str, body: str) -> dict:
    """An email the judge writes as Keyway or a vendor: built as a real .eml and read by the same
    reader as any inbox file, so the agents meet it exactly as they would meet a real one."""
    import re
    import tempfile
    from email.utils import formataddr

    from world.mess.render import _eml
    try:
        from world.mess.noise import VENDOR_DOMAINS
    except ImportError:
        VENDOR_DOMAINS = {}
    body = (body or "").strip()
    if not body:
        return {"ok": False, "error": "Write the email first"}
    if sender == "keyway":
        frm, to, cc = formataddr(("Keyway AP", "ap@keywaypm.com")), "controller@oaklinehomes.com", None
    else:
        v = next((v for v in _vendors() if v.id == sender), None)
        if v is None:
            return {"ok": False, "error": f"unknown sender {sender!r}"}
        domain = VENDOR_DOMAINS.get(sender, re.sub(r"[^a-z0-9]", "", v.name.lower()) + ".com")
        frm, to, cc = formataddr((v.name, f"billing@{domain}")), "ap@keywaypm.com", "controller@oaklinehomes.com"
    today = demo.rails().now()
    path = Path(tempfile.mkdtemp(prefix="console-mail-")) / f"written-{_state['runs'] + 1}.eml"
    path.write_bytes(_eml(frm, to, (subject or "").strip() or "(no subject)", body,
                          datetime.combine(today, time(15, 10), tzinfo=UTC), cc=cc))
    who = "Keyway Management" if sender == "keyway" else frm.split(" <")[0]
    return run_file(path, f"Email from {who}")


def tell_fact(text: str) -> dict:
    """A fact in plain words is a claim, not evidence: the investigator verifies it against the
    books and flags or hands on only what the documents prove."""
    from . import api
    text = (text or "").strip()
    if not 3 <= len(text) <= 400:
        return {"ok": False, "error": "Say the fact in a sentence (up to 400 characters)"}
    mirror, rails = api._mirror(), demo.rails()
    today = rails.now()
    _state["runs"] += 1
    n0 = len(pipeline.tasks(mirror))
    emit({"type": "console_started", "label": f"Fact: {text}", "kind": "fact", "day": today.isoformat()})
    emit({"type": "fact", "text": text})
    before = books_figures(rails, mirror, today)
    task = pipeline.enqueue(mirror, "investigator", "portfolio",
                            f"Someone told the controller: \u201c{text}\u201d", {"fact": text}, today,
                            "console", key=f"console{_state['runs']}")
    emit({"type": "agents_woken", "tasks": [task]})
    worked = pipeline.process(rails, mirror, today, limit=12)
    delivered = demo._deliver(rails, mirror, today)
    out = {"ok": True, "label": f"Fact: {text}", "kind": "fact", "emails": len(delivered),
           "books": {"before": before, "after": books_figures(rails, mirror, today)},
           "usage": _usage_since(n0, mirror), "day": today.isoformat()}
    emit({"type": "console_done", **out, "decisions": [
        {k: w.get(k) for k in ("agent", "invoice_id", "decision", "verdict", "summary", "rationale")} for w in worked]})
    return out


def advance(days: int) -> dict:
    """Let time pass: each day is the real daily run (paperwork arrives, the investigator sweeps,
    the chaser and the anomaly agent work), exactly as Live investigation runs it."""
    from . import api
    days = max(1, min(int(days), 15))
    mirror, rails = api._mirror(), demo.rails()
    _state["runs"] += 1
    n0 = len(pipeline.tasks(mirror))
    start = rails.now()
    emit({"type": "console_started", "label": f"+{days} day{'s' if days > 1 else ''}", "kind": "time",
          "day": start.isoformat()})
    before = books_figures(rails, mirror, start)
    emails, ran = 0, 0
    for _ in range(days):
        if rails.now() >= demo.END:
            break
        res = demo.next_day(rails, mirror)
        ran += 1
        emails += len(res.get("delivered") or [])
    today = rails.now()
    out = {"ok": True, "label": f"+{ran} day{'s' if ran != 1 else ''} ({start} \u2192 {today})", "kind": "time",
           "emails": emails, "books": {"before": before, "after": books_figures(rails, mirror, today)},
           "usage": _usage_since(n0, mirror), "day": today.isoformat(),
           "note": None if ran == days else f"the demo calendar ends {demo.END}; reset to replay"}
    emit({"type": "console_done", **out})
    return out
