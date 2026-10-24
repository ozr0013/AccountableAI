"""Agreement agent as a JSON API: upload one agreement or invoice, get its review back.

    python -m agreement_agent.api            # http://127.0.0.1:8772/docs

POST /review  (multipart file; Claude drives the tools, about $0.03 per document;
              ?model=false runs the free fixed rules instead)
  in:  a PDF or .txt agreement or invoice
  out: Review: kind, every required field (found / missing / unclear, with the verbatim quote
       and where it is), complete, and a drafted escalation to the owner if anything is missing.
Nothing is sent or stored: the escalation is a draft and the upload stays in memory.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from hashlib import sha256

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, UploadFile

from .graph import review
from .models import Review, SourceDoc

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

MAX_BYTES = 10 * 1024 * 1024
ALLOWED = {"application/pdf", "text/plain"}

app = FastAPI(title="Agreement agent", description=__doc__ or "")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model_available": bool(os.getenv("ANTHROPIC_API_KEY"))}


@app.post("/review", response_model=Review)
async def review_document(file: UploadFile = File(...),
                          model: bool = Query(True, description="Claude drives the tools; false runs the fixed rules")) -> Review:
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
    return review(doc, use_model=model)


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("AGREEMENT_API_PORT", "8772")))


if __name__ == "__main__":
    main()
