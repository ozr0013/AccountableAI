"""Stand-ins for every Protocol in contracts/steps.py.

Deterministic, no network, no LLM. They read contracts/fixtures/sample/ (scenario 3)
and return canned-but-plausible results so anyone can build against them today.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from ..models import (
    AgreementTerms,
    AlertDraft,
    Briefing,
    Case,
    Cause,
    CauseFinding,
    ChaseMessage,
    ChaseReply,
    Citation,
    Claim,
    Document,
    Event,
    LedgerLine,
    PaymentTrail,
    SourceKind,
    Term,
    TrailEntry,
    Verdict,
    VerdictStatus,
)
from ..policy import BANNED_TERMS

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sample"


def _load(name: str):
    return json.loads((FIXTURE / name).read_text())


def _cite(doc: Document, locator: str | None = None) -> Citation:
    return Citation(source_id=doc.id, kind=doc.kind, locator=locator)


class FakeRails:
    def __init__(self, fixture: Path = FIXTURE):
        self._docs = [Document(**d) for d in json.loads((fixture / "documents.json").read_text())]
        self._ledger = [LedgerLine(**r) for r in json.loads((fixture / "ledger.json").read_text())]
        self._events: list[Event] = []

    def append_event(self, event: Event) -> None:
        self._events.append(event)

    def events(self, manager_id=None):
        return [e for e in self._events if manager_id in (None, e.manager_id)]

    def documents(self, manager_id=None, invoice_id=None):
        out = [d for d in self._docs if manager_id in (None, d.manager_id)]
        if invoice_id:
            out = [d for d in out if d.data.get("invoice_id") in (None, invoice_id)]
        return out

    def ledger(self, manager_id):
        return [l for l in self._ledger if l.manager_id == manager_id]

    def unpaid(self, manager_id, as_of: date):
        return [l for l in self.ledger(manager_id) if l.paid_amount < l.amount]

    def late_months(self, manager_id):
        return {"2026-03": True}

    def reconcile(self, manager_id):
        lines = self.ledger(manager_id)
        funded = sum(l.funded_amount for l in lines)
        paid = sum(l.paid_amount for l in lines)
        owed = funded - paid
        return {"funded": funded, "paid": paid, "held": 0.0, "owed": owed,
                "diff": funded - (paid + 0.0 + owed)}

    def dependents_of(self, fact_id):
        return [l.invoice_id for l in self._ledger]

    def policy_check(self, action, actor, payload):
        if action in ("move_money", "send_unapproved"):
            return False, f"{action} is never allowed"
        if action == "approve" and payload.get("proposed_by") == actor:
            return False, "proposer cannot approve"
        return True, "ok"


class FakeAgents:
    def extract_terms(self, agreement: Document) -> AgreementTerms:
        return AgreementTerms(
            manager_id=agreement.manager_id or "keyway",
            vendor_payment_days=Term(value=10, citation=_cite(agreement, "7.2")),
            holdback_pct=Term(value=10, citation=_cite(agreement, "7.4")),
            holdback_condition=Term(value="until inspection passes", citation=_cite(agreement, "7.4")),
            approval_limit=Term(value=5000, citation=_cite(agreement, "8.1")),
        )

    def build_trail(self, line: LedgerLine, docs: list[Document]) -> PaymentTrail:
        claims = {
            SourceKind.manager_statement: "paid",
            SourceKind.manager_bank: "no payment found",
            SourceKind.vendor_notice: "not received",
            SourceKind.vendor_confirmation: "confirmed",
            SourceKind.funding: "funded",
            SourceKind.vendor_invoice: "invoiced",
        }
        entries = [
            TrailEntry(on=d.dated, kind=d.kind, claim=claims[d.kind], citation=_cite(d))
            for d in docs if d.kind in claims
        ]
        winners = [e.citation for e in entries
                   if e.kind in (SourceKind.manager_bank, SourceKind.vendor_notice)]
        return PaymentTrail(
            invoice_id=line.invoice_id,
            entries=entries,
            conflict=True,
            winning_claim="unpaid",
            winning_sources=winners,
            reasoning="Bank records and vendor notice outrank the manager statement.",
        )

    def classify_cause(self, line, trail, terms) -> CauseFinding:
        return CauseFinding(
            invoice_id=line.invoice_id,
            cause=Cause.unexplained,
            confidence=0.9,
            explanation="Funded, past the 10-day term, no hold/paperwork/owner reason on file.",
            citations=trail.winning_sources + [terms.vendor_payment_days.citation],
        )

    def draft_chase(self, manager_id, alerts, prior) -> ChaseMessage:
        level = ["polite", "firm", "escalation"][min(len(prior), 2)]
        return ChaseMessage(
            id=f"chase-{uuid.uuid4().hex[:8]}",
            manager_id=manager_id,
            invoice_ids=[i for a in alerts for i in a.draft.invoice_ids],
            template_id=f"tpl-{level}",
            level=level,
            body="Please confirm payment of the listed vendor invoices with proof of payment.",
        )

    def read_reply(self, reply_text, manager_id, docs) -> ChaseReply:
        return ChaseReply(
            id=f"reply-{uuid.uuid4().hex[:8]}",
            manager_id=manager_id,
            invoice_ids=[],
            text=reply_text,
            received_at=datetime.now(UTC),
            claims_paid="paid" in reply_text.lower(),
            proof=[],
        )

    def build_case(self, manager_id, alerts) -> Case:
        return Case(
            id=f"case-{uuid.uuid4().hex[:8]}",
            manager_id=manager_id,
            alerts=[a.draft.id for a in alerts],
            proposed_hold=sum(a.draft.amount for a in alerts),
            summary=f"{len(alerts)} open item(s); recommend holding the unpaid amount.",
            evidence=[c for a in alerts for cl in a.draft.claims for c in cl.citations],
        )


class FakeVerifier:
    """Rule-only stand-in: blocks uncited claims and banned wording. No refutation."""

    def verify(self, draft: AlertDraft, evidence: list[Document]) -> Verdict:
        known = {d.id for d in evidence}
        uncited = [c.text for c in draft.claims
                   if not c.citations or any(x.source_id not in known for x in c.citations)]
        text = " ".join([draft.headline, *(c.text for c in draft.claims)]).lower()
        banned = [t for t in BANNED_TERMS if t in text]
        status = VerdictStatus.blocked if (uncited or banned) else VerdictStatus.approved
        return Verdict(alert_id=draft.id, status=status, reasons=["fake verifier: rules only"],
                       uncited_claims=uncited, banned_terms=banned)


class FakeApprovals:
    def __init__(self):
        self._pending: dict[str, Case] = {}

    def request(self, case: Case) -> str:
        aid = f"appr-{uuid.uuid4().hex[:8]}"
        self._pending[aid] = case
        return aid

    def decide(self, approval_id, approver, approve) -> Case:
        case = self._pending.pop(approval_id)
        if approver == case.proposed_by:
            raise PermissionError("proposer cannot approve")
        if approve:
            case.approved_by = approver
        return case


class FakeBriefer:
    def brief(self, manager_id, question) -> Briefing:
        return Briefing(
            manager_id=manager_id,
            question=question,
            answer="1 open item: INV-KW-0301 $1,240 to Rio Grande Plumbing, unpaid since Mar 13.",
            claims=[Claim(text="Vendor reports INV-KW-0301 unpaid as of Apr 20",
                          citations=[Citation(source_id="doc-notice-riogrande-0420",
                                              kind=SourceKind.vendor_notice)])],
        )
