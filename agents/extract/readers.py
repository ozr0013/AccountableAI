"""Turn raw files into text: emails (with forwarded headers and attachments), PDFs, scans."""

from __future__ import annotations

import base64
import io
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from email import message_from_bytes, policy

log = logging.getLogger(__name__)


@dataclass
class Mail:
    from_name: str
    from_addr: str
    to: str
    subject: str
    sent: datetime | None
    body: str                                  # plain text, quoted replies included
    attachments: list[tuple[str, bytes]] = field(default_factory=list)

    @property
    def own_text(self) -> str:
        """The body without quoted reply lines (> ...) or anything after 'On ... wrote:'."""
        out = []
        for line in self.body.splitlines():
            if line.lstrip().startswith(">") or re.match(r"^On .+wrote:\s*$", line.strip()):
                break
            out.append(line)
        return "\n".join(out)


def read_eml(data: bytes) -> Mail:
    from email.utils import parseaddr

    from .fields import parse_datetime_header
    msg = message_from_bytes(data, policy=policy.default)
    name, addr = parseaddr(str(msg.get("From", "")))
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = body_part.get_content() if body_part else ""
    if body_part is not None and body_part.get_content_subtype() == "html":
        body = html_to_text(body)
    atts = [(a.get_filename() or "attachment", a.get_content()) for a in msg.iter_attachments()]
    return Mail(name, addr.lower(), str(msg.get("To", "")), str(msg.get("Subject", "")),
                parse_datetime_header(str(msg.get("Date", ""))), body,
                [(n, c if isinstance(c, bytes) else str(c).encode()) for n, c in atts])


def html_to_text(html: str) -> str:
    import html as h
    t = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return "\n".join(ln.strip() for ln in h.unescape(t).replace("\xa0", " ").splitlines()).strip()


@dataclass
class PdfText:
    text: str
    method: str          # text_layer | ocr_tesseract | ocr_vision | none
    pages: int


def pdf_text(data: bytes) -> PdfText:
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = len(pdf.pages)
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception as e:      # noqa: BLE001 - a broken PDF is just unreadable
        log.warning("pdf unreadable: %s", e)
        return PdfText("", "none", 0)
    if len(text.strip()) > 20:
        return PdfText(text, "text_layer", pages)
    return ocr(data, pages)


def _page_images(data: bytes, scale: float = 2.0):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(data)
    return [doc[i].render(scale=scale).to_pil() for i in range(len(doc))]


def ocr(data: bytes, pages: int) -> PdfText:
    """No text layer: tesseract if installed, else Claude vision if a key is set, else nothing."""
    mode = os.getenv("EXTRACT_OCR", "auto").lower()
    if mode in ("auto", "tesseract") and shutil.which("tesseract"):
        try:
            import pytesseract
            text = "\n".join(pytesseract.image_to_string(im) for im in _page_images(data))
            if text.strip():
                return PdfText(text, "ocr_tesseract", pages)
        except Exception as e:  # noqa: BLE001
            log.warning("tesseract failed: %s", e)
    if mode in ("auto", "vision") and os.getenv("ANTHROPIC_API_KEY"):
        text = _vision(data)
        if text:
            return PdfText(text, "ocr_vision", pages)
    return PdfText("", "none", pages)


def _vision(data: bytes) -> str:
    try:
        import anthropic
        im = _page_images(data, 1.5)[0]
        buf = io.BytesIO()
        im.save(buf, "PNG")
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=os.getenv("EXTRACT_VISION_MODEL", "claude-haiku-5-5"), max_tokens=1500,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.b64encode(buf.getvalue()).decode()}},
                {"type": "text", "text": "Transcribe this scanned document exactly as plain "
                                         "text, one line per printed line. No commentary."}]}])
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    except Exception as e:      # noqa: BLE001
        log.warning("vision OCR failed: %s", e)
        return ""
