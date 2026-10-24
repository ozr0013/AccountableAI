"""Model fallback for the invoices rules can't settle: an amount that mixes in an old balance, or
a scan whose OCR total disagrees with what Oakline wired. Haiku re-reads the invoice (the page
image when we have it) and answers in a typed schema. Off without ANTHROPIC_API_KEY or with
EXTRACT_LLM=off; the record then stays flagged for a person."""

from __future__ import annotations

import base64
import io
import logging
import os

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)
MODEL = os.getenv("EXTRACT_MODEL", "claude-haiku-5-5")

SYSTEM = ("You read contractor invoices for an accounts payable team. Report only what is "
          "printed. 'current_charges' is the amount for THIS invoice's own work: exclude any "
          "previous balance, past-due amount or balance forward, even if the printed total "
          "includes it. If you cannot read a number, set it to null.")


class InvoiceRead(BaseModel):
    current_charges: float | None = Field(description="This invoice's own charges, no old balance")
    printed_total: float | None = Field(description="The bottom-line total as printed")
    older_balance_included: bool = Field(description="Printed total includes an older balance")
    keyway_reference: str | None = Field(description="Customer PO / work order like KW-0301")


class Usage:
    calls = 0
    input_tokens = 0
    output_tokens = 0


def enabled() -> bool:
    return os.getenv("EXTRACT_LLM", "on").lower() != "off" and bool(os.getenv("ANTHROPIC_API_KEY"))


def reread_invoice(text: str, pdf: bytes | None = None) -> InvoiceRead | None:
    if not enabled():
        return None
    try:
        import anthropic
        content: list[dict] = []
        if pdf:
            from .readers import _page_images
            buf = io.BytesIO()
            _page_images(pdf, 1.5)[0].save(buf, "PNG")
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.b64encode(buf.getvalue()).decode()}})
        content.append({"type": "text", "text": "Invoice text (may contain OCR errors):\n"
                                                f"{text[:3000]}"})
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=MODEL, max_tokens=400, system=SYSTEM,
            tools=[{"name": "report", "description": "Report the invoice fields",
                    "input_schema": InvoiceRead.model_json_schema()}],
            tool_choice={"type": "tool", "name": "report"},
            messages=[{"role": "user", "content": content}])
        Usage.calls += 1
        Usage.input_tokens += resp.usage.input_tokens
        Usage.output_tokens += resp.usage.output_tokens
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use":
                return InvoiceRead(**block.input)
    except Exception as e:      # noqa: BLE001 - any failure leaves the record flagged
        log.warning("invoice re-read failed: %s", e)
    return None
