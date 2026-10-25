"""Agreement agent as a JSON API: upload one agreement or invoice, get its review back.

    python -m agreement_agent.api            # http://127.0.0.1:8772/docs

POST /review  (multipart file; Claude drives the tools, about $0.03 per document;
              ?model=false runs the free fixed rules instead)
  in:  a PDF or .txt agreement or invoice
  out: Review: kind, every required field (found / missing / unclear, with the verbatim quote
       and where it is), complete, and a drafted escalation to the owner if anything is missing.
Each review is saved to SpacetimeDB's `agreement` table (one row per document, replaced on a
re-review), and the rules derived from it (agreement_agent/rules_from_terms.py) to
`agreement_rule`. The file itself is not stored; the escalation stays a draft, never sent.

A complete agreement's rules go active. An incomplete one waits as `needs_controller` until the
controller confirms it by supplying the missing terms, or rejects it:
    GET  /agreements, GET /agreements/{doc_id}
    POST /agreements/{doc_id}/confirm  {"values": {"payment_due": "...", ...}, "note": "..."}
    POST /agreements/{doc_id}/reject   {"note": "..."}
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from .controller import ControllerError, confirm, reject
from .graph import review
from .models import Review, SourceDoc
from .rules_from_terms import agreement_status, rules_for

log = logging.getLogger(__name__)

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

MAX_BYTES = 10 * 1024 * 1024
ALLOWED = {"application/pdf", "text/plain"}

app = FastAPI(title="Agreement agent", description=__doc__ or "")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model_available": bool(os.getenv("ANTHROPIC_API_KEY"))}


def _opt(value: str | None) -> dict:
    return {"none": []} if value is None else {"some": value}


def agreement_row(r: Review, status: str, confirmed_by: str | None = None,
                  note: str | None = None) -> list:
    """Reducer arguments for save_agreement, in the module's order."""
    by = {f.name: f for f in r.fields}

    def value(name):
        f = by.get(name)
        return _opt(f.value if f and f.status == "found" else None)

    esc = r.escalation
    return [r.doc_id, r.filename, r.kind, value("owner_name"), value("vendor_name"),
            value("payment_due"), value("payment_amount"), value("payment_info"),
            value("effective_date"), r.complete,
            [f.name for f in r.fields if f.status != "found"],
            _opt(esc.subject if esc else None), _opt(esc.body if esc else None),
            json.dumps([f.model_dump(mode="json") for f in r.fields]), json.dumps(r.steps),
            r.mode, float(r.cost_usd), status, _opt(confirmed_by), _opt(note)]


def rule_rows(r: Review, rules: list[dict]) -> list:
    """Reducer arguments for set_agreement_rules."""
    vendor = next((f.value for f in r.fields if f.name == "vendor_name" and f.value), "") or ""
    # Object fields go over the wire in snake_case.
    return [r.doc_id, vendor, [{"rule_id": x["rule_id"], "rule_type": x["rule_type"],
                                "params": json.dumps(x["params"], sort_keys=True),
                                "check": x["check"], "status": x["status"],
                                "source_quote": _opt(x["source_quote"]),
                                "location": _opt(x["location"])} for x in rules]]


def _mirror():
    from rails.live.spacetime import SpacetimeMirror
    return SpacetimeMirror()


@contextmanager
def coordinator_lock(mirror):
    """Use the same cross-service lock as approvals and evidence mutations."""
    from rails.live.spacetime import SpacetimeMirror
    if not isinstance(mirror, SpacetimeMirror):
        yield
        return
    from rails.db import connect
    with connect() as conn:
        acquired = conn.execute('SELECT pg_try_advisory_lock(73920126)').fetchone()[0]
        if not acquired:
            raise RuntimeError('Coordinator busy; retry agreement mutation.')
        try:
            yield
        finally:
            conn.execute('SELECT pg_advisory_unlock(73920126)')


def save(r: Review, rules: list[dict], status: str, confirmed_by: str | None = None,
         note: str | None = None, *, coordinator_locked: bool = False) -> str | None:
    """Save the agreement and its rules to SpacetimeDB. Returns an error message, or None."""
    try:
        mirror = _mirror()
        with nullcontext() if coordinator_locked else coordinator_lock(mirror):
            mirror.call("save_agreement", agreement_row(r, status, confirmed_by, note))
            if r.kind == "agreement":
                mirror.call("set_agreement_rules", rule_rows(r, rules))
        return None
    except Exception as exc:  # noqa: BLE001 - the review is still returned to the caller
        log.warning("agreement %s not saved to SpacetimeDB: %s", r.doc_id, exc)
        return f"{type(exc).__name__}: {exc}"[:300]


def wake_workflow() -> dict:
    """Wake the consumer after saving; its polling scan repairs a missed wake after restart.
    Then hand off to the chaser agent: queue a chaser task for every invoice now covered by an
    agreement (queueing only - the agents run on /pipeline/run or the worker)."""
    base = 'http://127.0.0.1:' + os.getenv('CHASER_API_PORT', '8773')
    request = urllib.request.Request(
        base + '/workflow/tick',
        data=b'{}', headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=10):
            pass
        with urllib.request.urlopen(urllib.request.Request(
                base + '/pipeline/dispatch', data=b'{}', method='POST',
                headers={'Content-Type': 'application/json'}), timeout=10) as resp:
            queued = json.loads(resp.read() or b'{}').get('queued', [])
        return {'triggered': True, 'chaser_tasks_queued': len(queued)}
    except (OSError, urllib.error.URLError) as exc:
        log.warning('Workflow wake deferred: %s', type(exc).__name__)
        return {'triggered': False, 'error': 'Saved; coordinator will retry on its next polling tick.'}


@app.post("/review")
async def review_document(file: Annotated[UploadFile, File(...)],
                          model: bool = Query(True, description="Claude drives the tools; false runs the fixed rules")) -> dict:
    data = await file.read(MAX_BYTES + 1)
    mime = file.content_type or "application/octet-stream"
    if file.filename and file.filename.lower().endswith(".txt"):
        mime = "text/plain"
    if mime not in ALLOWED:
        raise HTTPException(415, "upload a PDF or a .txt file")
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(413, "file must be between 1 byte and 10 MB")
    if model and not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(400, "Claude mode needs ANTHROPIC_API_KEY; pass ?model=false for the free rules")
    doc = SourceDoc(id=sha256(data).hexdigest()[:16], source="upload",
                    filename=file.filename or "upload", mime=mime, data=data,
                    received_at=datetime.now(UTC))
    result = review(doc, use_model=model)
    rules = rules_for(result)
    status = agreement_status(result, rules)
    error = save(result, rules, status)
    return {"review": result.model_dump(mode="json"), "status": status, "rules": rules,
            "saved_to_spacetime": error is None, "save_error": error,
            "workflow": wake_workflow() if error is None else {'triggered': False, 'error': 'Review was not saved.'}}


# -- controller sign-off -----------------------------------------------------------------

_DOC_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


APPROVER = "controller"


def controller() -> str:
    """Who human decisions are recorded as. No sign-in: every decision is the controller's."""
    return APPROVER


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, str] = Field(description="missing required term -> the controller's value")
    note: str | None = Field(default=None, max_length=1000)


class RejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(min_length=1, max_length=1000)


def _load(doc_id: str) -> tuple[dict, list[dict]]:
    if not _DOC_ID.match(doc_id):
        raise HTTPException(422, "invalid doc_id")
    mirror = _mirror()
    rows = mirror.query(f"SELECT * FROM agreement WHERE doc_id = '{doc_id}'")
    if not rows:
        raise HTTPException(404, "agreement not found")
    rules = [{"rule_id": r["rule_id"], "rule_type": r["rule_type"], "params": json.loads(r["params"]),
              "check": r["check"], "status": r["status"], "source_quote": r["source_quote"],
              "location": r["location"]}
             for r in mirror.query(f"SELECT * FROM agreement_rule WHERE doc_id = '{doc_id}'")]
    return rows[0], rules


def _public(row: dict, rules: list[dict]) -> dict:
    keep = ("doc_id", "filename", "kind", "status", "vendor_name", "payment_due", "payment_amount",
            "payment_info", "complete", "missing", "escalation_subject", "confirmed_by",
            "controller_note")
    return {**{k: row.get(k) for k in keep}, "rules": rules}


@app.get("/agreements")
def list_agreements() -> list[dict]:
    rows = _mirror().query("SELECT * FROM agreement")
    return [_public(r, []) for r in rows]


@app.get("/agreements/{doc_id}")
def get_agreement(doc_id: str) -> dict:
    return _public(*_load(doc_id))


def _decide(doc_id: str, decide) -> dict:
    with coordinator_lock(_mirror()):
        row, rules = _load(doc_id)
        try:
            result, new_rules, status, note, by = decide(row, rules)
        except ControllerError as exc:
            raise HTTPException(409, str(exc)) from exc
        error = save(result, new_rules, status, by, note, coordinator_locked=True)
        if error:
            raise HTTPException(502, f"not saved to SpacetimeDB: {error}")
        public = _public(*_load(doc_id))
    return {**public, 'workflow': wake_workflow()}


@app.post("/agreements/{doc_id}/confirm")
def confirm_agreement(doc_id: str, body: ConfirmIn, by: str = Depends(controller)) -> dict:
    def decide(row, _rules):
        result, rules = confirm(row, body.values, by, body.note)
        return result, rules, "confirmed", body.note, by
    return _decide(doc_id, decide)


@app.post("/agreements/{doc_id}/reject")
def reject_agreement(doc_id: str, body: RejectIn, by: str = Depends(controller)) -> dict:
    def decide(row, rules):
        result, rules = reject(row, rules, by)
        return result, rules, "rejected", body.note, by
    return _decide(doc_id, decide)


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("AGREEMENT_API_PORT", "8772")))


if __name__ == "__main__":
    main()
