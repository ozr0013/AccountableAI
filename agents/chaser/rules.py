"""Chaser by rules: approved templates only, fixed escalation order, proof-checked replies."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime

from contracts.models import Alert, ChaseMessage, ChaseReply, Citation, Document
from rails.guards import APPROVED_TEMPLATES, render
from rails.guards.templates import LEVEL_ORDER

Names = Callable[[str], str]
_PAID = re.compile(r"\b(paid|sent|remitted|transferred|cleared|settled|wired)\b", re.IGNORECASE)
_NOT = re.compile(r"\b(not|n't|never|haven't|hasn't|didn't|yet to|will be|going to)\b[^.]{0,30}"
                  r"\b(paid|sent|remitted|transferred|cleared|settled|wired)\b", re.IGNORECASE)
_INV = re.compile(r"\bINV-[A-Z]{2}-\d{3,4}\b")


def default_names(entity_id: str) -> str:
    return entity_id.removeprefix("v-").replace("-", " ").title()


def next_level(prior: list[ChaseMessage]) -> str:
    sent = [p.level for p in prior if p.sent_at]
    if not sent:
        return LEVEL_ORDER[0]
    i = LEVEL_ORDER.index(sent[-1])
    return LEVEL_ORDER[min(i + 1, len(LEVEL_ORDER) - 1)]


def vendor_of(alert: Alert) -> str:
    """Vendor id from the alert headline '<INV> (<vendor>): ...' written by the workflow."""
    m = re.search(r"\((v-[\w-]+)\)", alert.draft.headline)
    return m.group(1) if m else "the vendor"


def draft_chase(manager_id: str, alerts: list[Alert], prior: list[ChaseMessage],
                names: Names = default_names) -> ChaseMessage:
    level = next_level(prior)
    template_id = f"tpl-{level}"
    assert APPROVED_TEMPLATES[template_id]["level"] == level
    invoices = sorted({i for a in alerts for i in a.draft.invoice_ids})
    vendors = sorted({names(vendor_of(a)) for a in alerts})
    body = render(template_id, manager_name=names(manager_id),
                  amount=f"${sum(a.draft.amount for a in alerts):,.2f}",
                  vendor_name=", ".join(vendors), invoice_ref=", ".join(invoices))
    return ChaseMessage(id=f"chase-{manager_id}-{level}-{'-'.join(invoices)}",
                        manager_id=manager_id, invoice_ids=invoices, template_id=template_id,
                        level=level, body=body)


def proof_docs(invoice_ids: list[str], docs: list[Document]) -> list[Citation]:
    """Records that actually show payment: a bank payment or a vendor 'paid' confirmation."""
    from rails.guards.payments import payment_proof
    return payment_proof(invoice_ids, docs)


def claims_paid(text: str) -> bool:
    return bool(_PAID.search(text)) and not _NOT.search(text)


def read_reply(reply_text: str, manager_id: str, docs: list[Document],
               invoice_ids: list[str] | None = None) -> ChaseReply:
    in_docs = sorted({d.data["invoice_id"] for d in docs if d.data.get("invoice_id")})
    ids = invoice_ids or sorted(set(_INV.findall(reply_text))) or in_docs
    paid = claims_paid(reply_text)
    return ChaseReply(
        id=f"reply-{manager_id}-{hashlib.sha1(reply_text.encode()).hexdigest()[:8]}",
        manager_id=manager_id, invoice_ids=ids, text=reply_text,
        received_at=datetime.now(UTC), claims_paid=paid,
        proof=proof_docs(ids, docs) if paid else [])
