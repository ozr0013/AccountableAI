"""Local source files -> text documents with immutable byte and page provenance.

Source trust is supplied by the operator (kind), never inferred from a filename or model.
This reader does not OCR images, open remote resources, or promote email into bank proof.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

from contracts.models import Document, SourceKind
from rails.serialization import digest

MAX_BYTES = 25 * 1024 * 1024


@dataclass
class FileDocument:
    document: Document
    provenance: dict
    warnings: list[str] = field(default_factory=list)


class FileReadError(ValueError):
    pass


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        elif tag in ("p", "div", "br", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden-1)
        elif tag in ("p", "div", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def read_file(path, kind, manager_id="keyway", dated=None):
    path = Path(path)
    if not path.is_file():
        raise FileReadError(f"source file does not exist: {path}")
    if path.stat().st_size > MAX_BYTES:
        raise FileReadError("file exceeds the 25 MiB import limit")
    kind = SourceKind(kind)
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    suffix = path.suffix.lower()
    provenance = {"filename": path.name, "sha256": sha, "bytes": len(raw), "format": suffix,
                  "pages": [], "attachments": []}
    warnings = []
    if suffix == ".pdf":
        try:
            from io import BytesIO

            from pypdf import PdfReader
            pdf = PdfReader(BytesIO(raw), strict=True)
            if pdf.is_encrypted:
                raise FileReadError("encrypted PDF needs an unlocked copy")
            texts = []
            offset = 0
            for number, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                if not text.strip():
                    warnings.append(f"page {number} has no extractable text; OCR or manual review required")
                texts.append(text)
                provenance["pages"].append({"page": number, "start": offset, "end": offset+len(text)})
                offset += len(text)+2
            text = "\n\n".join(texts)
            provenance["reader"] = "pypdf"
        except ImportError as exc:
            raise FileReadError("PDF support requires: pip install -e '.[ingestion]'") from exc
        except FileReadError:
            raise
        except Exception as exc:
            raise FileReadError(f"PDF could not be read: {type(exc).__name__}") from exc
    elif suffix == ".eml":
        if kind not in (SourceKind.manager_reply, SourceKind.vendor_notice):
            raise FileReadError("exported emails must be imported as manager_reply or vendor_notice; email is a claim, not bank proof")
        message = BytesParser(policy=policy.default).parsebytes(raw)
        if dated is None and message.get("Date"):
            try:
                dated = parsedate_to_datetime(str(message["Date"])).date()
            except (TypeError, ValueError, OverflowError):
                warnings.append("email Date header is invalid; date remains unknown")
        body = message.get_body(preferencelist=("plain", "html"))
        if body is None:
            raise FileReadError("email has no readable text body")
        try:
            body_text = body.get_content()
        except (LookupError, UnicodeError) as exc:
            raise FileReadError("email encoding could not be decoded") from exc
        if body.get_content_type() == "text/html":
            html = _HTMLText()
            html.feed(body_text)
            body_text = "".join(html.parts)
        headers = "\n".join(f"{name}: {message.get(name, '')}" for name in ("From", "To", "Subject", "Date"))
        text = headers + "\n\n" + body_text.strip()
        for attachment in message.iter_attachments():
            content = attachment.get_payload(decode=True) or b""
            provenance["attachments"].append({"filename": attachment.get_filename(),
                "content_type": attachment.get_content_type(), "sha256": hashlib.sha256(content).hexdigest()})
        if provenance["attachments"]:
            warnings.append("email attachments require separate import with an explicit source kind")
        provenance["reader"] = "email.parser"
    elif suffix in (".txt", ".csv", ".tsv", ".json", ".md"):
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise FileReadError("text data must be UTF-8; convert this file and retry") from exc
        provenance["reader"] = "utf-8"
    else:
        raise FileReadError("supported inputs: .pdf, .txt, .csv, .tsv, .json, .md and .eml")
    if not text.strip():
        raise FileReadError("no readable text; scanned PDFs need OCR or a text export")
    if suffix == ".pdf" and warnings:
        # Partial extraction cannot silently omit a payment row or a contract clause.
        raise FileReadError("; ".join(warnings))
    doc = Document(id=f"file-{digest([sha, kind.value, manager_id])[:24]}", kind=kind,
                   manager_id=manager_id, dated=dated, text=text, data={})
    return FileDocument(doc, provenance, warnings)
