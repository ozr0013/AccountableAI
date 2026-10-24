"""Parser: a file (PDF, text, email body) -> text, header, numbered sections, document kind.

No model calls. Scanned PDFs with no text layer come back with empty text; the agent then
reports every field as unclear ("no readable text") instead of guessing.
"""

from __future__ import annotations

import io
import re

from .models import Parsed, Section, SourceDoc

HEADER_LINES = 8
# "4. Payment Terms. ...", "4.2 Fees", "Section 4 Payment" (a bare "30 days" wrap is not a section)
_NUMBERED = re.compile(r"^\s*(?:(?:section|article|clause)\s+(\d+(?:\.\d+)*)[.):]?|(\d{1,2}(?:\.\d+)*)(?:[.)]|(?=\.\d)))\s+([A-Z].*)$", re.IGNORECASE)
_CAPS = re.compile(r"^\s*([A-Z][A-Za-z &/,'-]{3,40})\s*:?\s*$")

_AGREEMENT = ("agreement", "contract", "between", "parties", "hereby", "term of", "shall", "services", "terms",
              "fees", '("owner")', '("client")', '("vendor")', '("contractor")')
_INVOICE = ("invoice", "bill to", "amount due", "invoice no", "invoice number", "balance due", "remit")


def extract_text(doc: SourceDoc) -> list[str]:
    """Pages of text. PDFs page by page; anything else is one page."""
    name = doc.filename.lower()
    if doc.mime == "application/pdf" or name.endswith(".pdf"):
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(doc.data))
            return [(p.extract_text() or "") for p in reader.pages]
        except Exception:                                   # noqa: BLE001 - unreadable PDF
            return [""]
    return [doc.data.decode("utf-8", errors="replace")]


def _clean(page: str) -> str:
    page = page.replace("\r", "").replace(" ", " ")
    page = re.sub(r"[ \t]+", " ", page)
    page = re.sub(r"(\w)-\n(\w)", r"\1\2", page)            # re-join hyphenated line breaks
    return re.sub(r"\n{3,}", "\n\n", page).strip()


def split_sections(pages: list[str]) -> list[Section]:
    out: list[Section] = []
    cur: Section | None = None
    for pno, page in enumerate(pages, 1):
        for line in page.split("\n"):
            m = _NUMBERED.match(line)
            caps = _CAPS.match(line) if not m else None
            if m or caps:
                if cur:
                    out.append(cur)
                if m:
                    number, rest = m.group(1) or m.group(2), m.group(3).strip()
                    # "4.2 Payment. Vendor shall ..." -> title "Payment", body keeps the sentence
                    title, _, body = rest.partition(". ")
                    cur = Section(number=number, title=title.strip(" .:"), text=rest if body else "", page=pno)
                else:
                    cur = Section(number="", title=caps.group(1).title(), text="", page=pno)
                continue
            if cur is None:
                cur = Section(number="", title="Preamble", text="", page=pno)
            cur.text = f"{cur.text}\n{line}".strip()
    if cur:
        out.append(cur)
    return [s for s in out if s.text or s.title]


def classify(text: str, filename: str = "") -> tuple[str, str]:
    t = f"{filename} {text[:3000]}".lower()
    a = [w for w in _AGREEMENT if w in t]
    i = [w for w in _INVOICE if w in t]
    if not text.strip():
        return "other", "no readable text (a scan without a text layer?)"
    if len(i) >= 2 and len(i) >= len(a) - 2 and "invoice" in t:
        return "invoice", f"invoice wording: {', '.join(i)}"
    if len(a) >= 3:
        return "agreement", f"agreement wording: {', '.join(a)}"
    return "other", "neither agreement nor invoice wording"


def parse(doc: SourceDoc) -> Parsed:
    pages = [_clean(p) for p in extract_text(doc)]
    text = "\n\n".join(pages).strip()
    header = [line.strip() for line in text.split("\n") if line.strip()][:HEADER_LINES]
    kind, why = classify(text, doc.filename)
    return Parsed(doc_id=doc.id, text=text, pages=pages, header=header,
                  sections=split_sections(pages), kind=kind, kind_reason=why)
