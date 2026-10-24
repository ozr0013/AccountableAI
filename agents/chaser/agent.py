"""Chaser agent (task 5): reminders from approved templates; replies read with proof checks.

Drafting is code only: the body is a controller-approved template, so no model writes outbound
text. Reading a reply is the judgment part: the LLM reads free text ("this one was paid last
week", "waiting on their W-9") and says whether it claims payment and for which invoices.
Proof is never the reply's word: guards accept only bank records or vendor confirmations that
show the payment for those invoices.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, Field

from contracts.models import Alert, ChaseMessage, ChaseReply, Document

from ..llm import StructuredLLM, guarded_call
from . import rules
from .prompts import SYSTEM


class ReplyReading(BaseModel):
    claims_paid: bool = Field(description="does the reply claim the vendor was paid?")
    invoice_ids: list[str] = Field(description="invoice ids the reply is about")
    proof_source_ids: list[str] = Field(
        description="ids of bank records or vendor confirmations that show the payment")
    summary: str = Field(description="one neutral sentence")


class ChaserAgent:
    def __init__(self, llm: StructuredLLM | None = None, names: rules.Names = rules.default_names):
        self.llm = llm
        self.names = names

    def draft_chase(self, manager_id: str, alerts: list[Alert],
                    prior: list[ChaseMessage]) -> ChaseMessage:
        return rules.draft_chase(manager_id, alerts, prior, self.names)

    def read_reply(self, reply_text: str, manager_id: str, docs: list[Document],
                   invoice_ids: list[str] | None = None) -> ChaseReply:
        base = rules.read_reply(reply_text, manager_id, docs, invoice_ids)
        known = sorted({d.data.get("invoice_id") for d in docs if d.data.get("invoice_id")})
        if not known and not base.invoice_ids:
            return base

        def check(r: ReplyReading) -> list[str]:
            p = [f"unknown invoice {i!r}" for i in r.invoice_ids if i not in known]
            valid = {c.source_id for c in rules.proof_docs(r.invoice_ids or base.invoice_ids, docs)}
            p += [f"{s} is not a bank record or vendor confirmation showing this payment"
                  for s in r.proof_source_ids if s not in valid]
            return p

        prompt = (f"<reply>\n{reply_text}\n</reply>\n<context>\n"
                  + json.dumps({"manager_id": manager_id, "open_invoices": known}) + "\n</context>\n"
                  + "<records>\n" + "\n".join(f'<doc id="{d.id}" kind="{d.kind.value}">{d.text}</doc>'
                                              for d in docs) + "\n</records>")
        ans, _ = guarded_call(self.llm, ReplyReading, SYSTEM, prompt, check)
        if ans is None:
            return base
        ids = ans.invoice_ids or base.invoice_ids
        proof = [c for c in rules.proof_docs(ids, docs) if c.source_id in ans.proof_source_ids]
        return base.model_copy(update={
            "id": f"reply-{manager_id}-{hashlib.sha1(reply_text.encode()).hexdigest()[:8]}",
            "invoice_ids": ids, "claims_paid": ans.claims_paid,
            "proof": proof if ans.claims_paid else []})
