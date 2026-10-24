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
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, UploadFile

from .graph import review
from .models import Review, SourceDoc
from .rules_from_terms import rules_for

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


def agreement_row(r: Review) -> list:
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
            r.mode, float(r.cost_usd)]


def rule_rows(r: Review, rules: list[dict]) -> list:
    """Reducer arguments for set_agreement_rules."""
    vendor = next((f.value for f in r.fields if f.name == "vendor_name" and f.value), "") or ""
    # Object fields go over the wire in snake_case.
    return [r.doc_id, vendor, [{"rule_id": x["rule_id"], "rule_type": x["rule_type"],
                                "params": json.dumps(x["params"], sort_keys=True),
                                "check": x["check"], "status": x["status"],
                                "source_quote": _opt(x["source_quote"]),
                                "location": _opt(x["location"])} for x in rules]]


def save(r: Review, rules: list[dict]) -> str | None:
    """Save the agreement and its rules to SpacetimeDB. Returns an error message, or None."""
    from rails.live.spacetime import SpacetimeMirror
    try:
        mirror = SpacetimeMirror()
        mirror.call("save_agreement", agreement_row(r))
        if r.kind == "agreement":
            mirror.call("set_agreement_rules", rule_rows(r, rules))
        return None
    except Exception as exc:  # noqa: BLE001 - the review is still returned to the caller
        log.warning("agreement %s not saved to SpacetimeDB: %s", r.doc_id, exc)
        return f"{type(exc).__name__}: {exc}"[:300]


@app.post("/review")
async def review_document(file: UploadFile = File(...),
                          model: bool = Query(True, description="Claude drives the tools; false runs the fixed rules")) -> dict:
    data = await file.read()
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
    error = save(result, rules)
    return {"review": result.model_dump(mode="json"), "rules": rules,
            "saved_to_spacetime": error is None, "save_error": error}


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("AGREEMENT_API_PORT", "8772")))


if __name__ == "__main__":
    main()
