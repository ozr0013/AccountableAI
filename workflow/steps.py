"""Workflow steps. Plain code decides what runs; agents (via contracts.steps.Agents) judge.

Each public method is one LangGraph node: it takes the WorkflowState and returns a dict of
updates. Dependencies come from contracts.registry, so every step runs on stand-ins today and
on real rails / agents / verifier once their env var says "real".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from langgraph.types import interrupt

from contracts import registry
from contracts.models import (
    AlertDraft,
    AlertKind,
    Case,
    Cause,
    Citation,
    Claim,
    Document,
    Event,
    LedgerLine,
    SourceKind,
    VerdictStatus,
)
from contracts.steps import Agents, Approvals, Rails, Verifier
from rails.facts import current_terms

from .book import WF_TYPES, WorkflowBook, alert_id, digest, done_event, wf_event
from .state import Assessment, Route, WorkflowState
from .terms import terms_for

ALERT_FOR_CAUSE = {
    Cause.unexplained: AlertKind.unexplained_unpaid,
    Cause.paperwork_pending: AlertKind.paperwork_request,
    Cause.owner_side: AlertKind.owner_pending_approval,
}
ACTION_FOR_KIND = {
    AlertKind.unexplained_unpaid: "Chase the manager for bank or vendor proof of payment.",
    AlertKind.paperwork_request: "Draft a request for the missing vendor paperwork (needs approval).",
    AlertKind.owner_pending_approval: "Oakline's own approval/funding is pending; no manager action.",
    AlertKind.duplicate_billing: "Don't fund this invoice again; ask the manager to return or credit the duplicate.",
    AlertKind.recurring_pattern: ("Recommend stricter terms: a shorter payment window, proof of vendor "
                                  "payment with each statement, and a review before the next funding."),
}
ITEM_KINDS = set(ALERT_FOR_CAUSE.values())
# Recurring-lateness alert (scenario 4): late in at least this many of the last N months.
PATTERN_MIN_LATE, PATTERN_WINDOW = 3, 6
_IGNORED = WF_TYPES | {"entity_registered", "world_meta", "period_closed",
                       "approval_requested", "approval_decided"}


@dataclass(frozen=True)
class Deps:
    rails: Rails
    agents: Agents
    verifier: Verifier
    approvals: Approvals

    @classmethod
    def from_registry(cls, rails: Rails | None = None) -> Deps:
        return cls(rails or registry.rails(), registry.agents(), registry.verifier(),
                   registry.approvals())


def _term_values(t) -> dict:
    return {k: getattr(t, k).value for k in ("vendor_payment_days", "holdback_pct", "approval_limit")
            if getattr(t, k) is not None}


def _cite(docs: list[Document]) -> list[Citation]:
    return [Citation(source_id=d.id, kind=d.kind) for d in docs]


def _month_after(ym: str) -> str:
    y, m = map(int, ym.split("-"))
    return f"{y + m // 12}-{m % 12 + 1:02d}"


def _cents(x: float) -> int:
    return round(x * 100)


class Steps:
    def __init__(self, deps: Deps):
        self.d = deps

    # -- routing (code only) ---------------------------------------------------------------
    def route(self, s: WorkflowState) -> dict:
        e, now = s.event, s.event.learned_at.date()
        route = self._route_for(e)
        ids: list[str] = []
        if route in (Route.assess, Route.reply):
            ids = self._affected_invoices(e)
        elif route == Route.scan:
            ids = [l.invoice_id for l in self.d.rails.ledger(e.manager_id)]
        return {"now": now, "route": route, "manager_id": e.manager_id, "invoice_ids": ids,
                "trace": [f"{e.type} {e.id} -> {route.value} ({len(ids)} invoice(s))"]}

    @staticmethod
    def _route_for(e: Event) -> Route:
        if e.type in _IGNORED:
            return Route.ignore
        if e.type == "document_ingested":
            kind, data = e.payload.get("kind"), e.payload.get("data", {})
            if kind == SourceKind.agreement:
                return Route.agreement
            if data.get("status") == "bank_change_request":
                return Route.bank_change
            return Route.assess
        return {
            "fact_corrected": Route.assess, "terms_confirmed": Route.assess,
            "chase_reply": Route.reply, "bank_change_requested": Route.bank_change,
            "scan_requested": Route.scan, "funding_review_requested": Route.funding_review,
        }.get(e.type, Route.ignore)

    def _affected_invoices(self, e: Event) -> list[str]:
        """Invoices whose facts this event touched, per rails.dependents_of, plus any it names."""
        named = {e.invoice_id, *e.payload.get("invoice_ids", []),
                 e.payload.get("data", {}).get("invoice_id")} - {None}
        return sorted(set(self.d.rails.dependents_of(e.id)) | named)

    # -- assess: payment trail + delay cause per invoice ----------------------------------
    def assess(self, s: WorkflowState) -> dict:
        lines = {l.invoice_id: l for l in self._lines(s)}
        out, notes = [], []
        for iid in s.invoice_ids:
            line = lines.get(iid)
            if line is None:
                notes.append(f"{iid}: not on a ledger yet")
                continue
            a = self._assess_one(line, s.now)
            out.append(a)
            notes.append(f"{iid}: {a.status}" + (f", cause {a.finding.cause.value}" if a.finding else "")
                         + (f" ({a.note})" if a.note else ""))
        return {"assessments": out, "trace": notes}

    def _lines(self, s: WorkflowState) -> list[LedgerLine]:
        managers = {s.manager_id} if s.manager_id else set()
        if not managers:   # e.g. a fact correction without manager: find the invoice's manager
            managers = {d.manager_id for d in self.d.rails.documents()
                        if d.kind == SourceKind.vendor_invoice and d.data.get("invoice_id") in s.invoice_ids}
        return [l for m in sorted(x for x in managers if x) for l in self.d.rails.ledger(m)]

    def _assess_one(self, line: LedgerLine, now: date) -> Assessment:
        base = {"invoice_id": line.invoice_id, "manager_id": line.manager_id}
        if line.duplicate_of:
            return Assessment(**base, status="duplicate", note=f"duplicate of {line.duplicate_of}")
        unpaid = _cents(line.paid_amount) < _cents(line.amount)
        if unpaid and self._bank_change_held(line):
            # Scenario 7: the payment is on hold by our own gate, so the manager isn't at fault.
            return Assessment(**base, status="held", note="payment held: vendor bank-detail change "
                              "awaits identity check and controller approval")
        paid_late = (not unpaid and line.paid_on and line.due_date and line.paid_on > line.due_date)
        if not unpaid and not paid_late:
            return Assessment(**base, status="paid")
        if unpaid and (line.due_date is None or now <= line.due_date):
            return Assessment(**base, status="not_due", note=f"due {line.due_date}")
        terms = terms_for(self.d.rails, self.d.agents, line.manager_id)
        if terms is None:
            return Assessment(**base, status="skipped", note="no agreement terms on file")
        docs = self._evidence(line.manager_id, [line.invoice_id], now)
        trail = self.d.agents.build_trail(line, docs)
        finding = self.d.agents.classify_cause(line, trail, terms)
        end = line.paid_on if not unpaid else now
        return Assessment(**base, status="delayed", days_late=(end - line.due_date).days,
                          trail=trail, finding=finding)

    def _evidence(self, manager_id: str, invoice_ids: list[str], now: date | None) -> list[Document]:
        seen: dict[str, Document] = {}
        for iid in invoice_ids:
            for d in self.d.rails.documents(manager_id, iid):
                if now is None or d.dated is None or d.dated <= now:
                    seen.setdefault(d.id, d)
        return list(seen.values())

    def _verify_evidence(self, draft: AlertDraft, now: date | None) -> list[Document]:
        """The invoice's evidence, plus any manager document the draft cites (pattern alerts
        cite bank statements and the agreement, not one invoice)."""
        ev = {d.id: d for d in self._evidence(draft.manager_id, draft.invoice_ids, now)}
        cited = {c.source_id for cl in draft.claims for c in cl.citations} - ev.keys()
        ev |= {d.id: d for d in self.d.rails.documents(draft.manager_id) if d.id in cited}
        return list(ev.values())

    def _bank_change_held(self, line: LedgerLine) -> bool:
        return any(e.type == "bank_change_held" and e.invoice_id == line.invoice_id
                   for e in self.d.rails.events(line.manager_id))

    # -- alerts: draft (code) -> verifier -> record ----------------------------------------
    def draft_alerts(self, s: WorkflowState) -> dict:
        drafts = []
        for a in s.assessments:
            if a.status == "duplicate":
                drafts.append(self._duplicate_draft(a))
                continue
            kind = ALERT_FOR_CAUSE.get(a.finding.cause) if a.finding else None
            if kind is None:
                continue
            drafts.append(self._draft(a, kind))
        if s.route == Route.scan and s.manager_id and (p := self._pattern_draft(s.manager_id)):
            drafts.append(p)
        return {"drafts": drafts, "trace": [f"draft {d.id} ({d.kind.value})" for d in drafts]}

    def _duplicate_draft(self, a: Assessment) -> AlertDraft:
        """Scenario 8: the same work billed and funded twice."""
        line = next(l for l in self.d.rails.ledger(a.manager_id) if l.invoice_id == a.invoice_id)
        pair = {line.invoice_id, line.duplicate_of}
        docs = self.d.rails.documents(a.manager_id)
        funding = [d for d in docs if d.kind == SourceKind.funding and d.data.get("invoice_id") in pair]
        billed = [d for d in docs if d.kind in (SourceKind.manager_statement, SourceKind.vendor_invoice)
                  and (d.data.get("invoice_id") in pair
                       or any(l.get("invoice_id") in pair for l in d.data.get("lines", [])))]
        claims = [Claim(text=f"Oakline funded {line.duplicate_of} and {line.invoice_id} for the same "
                             f"work, ${line.funded_amount:,.2f} each", citations=_cite(funding))]
        if billed:
            claims.append(Claim(text=f"{line.invoice_id} was billed again for work already billed as "
                                     f"{line.duplicate_of}", citations=_cite(billed)))
        return AlertDraft(
            id=alert_id(AlertKind.duplicate_billing.value, a.manager_id, sorted(pair)),
            kind=AlertKind.duplicate_billing, manager_id=a.manager_id,
            invoice_ids=[line.invoice_id, line.duplicate_of], amount=line.funded_amount,
            headline=f"{line.invoice_id} ({line.vendor_id}): duplicate of {line.duplicate_of}, "
                     f"${line.funded_amount:,.2f} funded twice",
            claims=claims, recommended_action=ACTION_FOR_KIND[AlertKind.duplicate_billing])

    def _pattern_draft(self, manager_id: str) -> AlertDraft | None:
        """Scenario 4: a manager who pays vendors late month after month."""
        months = self.d.rails.late_months(manager_id)
        window = sorted(months)[-PATTERN_WINDOW:]
        late = [m for m in window if months[m]]
        if len(late) < PATTERN_MIN_LATE:
            return None
        terms = terms_for(self.d.rails, self.d.agents, manager_id)
        if terms is None:
            return None
        # A month's bank statement is dated in the month or the one after.
        bank = [d for d in self.d.rails.documents(manager_id)
                if d.kind == SourceKind.manager_bank and d.dated
                and any(_month_after(m) >= d.dated.strftime("%Y-%m") >= m for m in late)]
        days = terms.vendor_payment_days
        claims = [
            Claim(text=f"Vendors were paid late in {len(late)} of the last {len(window)} months "
                       f"({', '.join(late)})",
                  citations=_cite(bank)),
            Claim(text=f"The agreement requires vendor payment within {days.value} days of invoice receipt",
                  citations=[days.citation]),
        ]
        return AlertDraft(
            id=alert_id(AlertKind.recurring_pattern.value, manager_id, [f"late:{','.join(late)}"]),
            kind=AlertKind.recurring_pattern, manager_id=manager_id, invoice_ids=[],
            headline=f"{manager_id}: vendors paid late in {len(late)} of {len(window)} months",
            claims=claims, recommended_action=ACTION_FOR_KIND[AlertKind.recurring_pattern])

    def _draft(self, a: Assessment, kind: AlertKind) -> AlertDraft:
        line = next(l for l in self.d.rails.ledger(a.manager_id) if l.invoice_id == a.invoice_id)
        owed = round(line.amount - line.paid_amount, 2)
        if owed > 0:
            state = f"${owed:,.2f} unpaid, {a.days_late} days past due" if a.days_late > 0 \
                else f"${owed:,.2f} unpaid"
        else:
            state = f"${line.amount:,.2f} paid {a.days_late} days late"
        claims = [Claim(text=a.finding.explanation, citations=a.finding.citations)]
        if a.trail and a.trail.winning_sources:
            # Cite every source that spoke to payment, winners and overruled ones alike.
            payment = {SourceKind.manager_statement, SourceKind.manager_bank,
                       SourceKind.vendor_confirmation, SourceKind.vendor_notice,
                       SourceKind.manager_reply}
            cites = {c.source_id: c for c in a.trail.winning_sources}
            for e in a.trail.entries:
                if e.kind in payment:
                    cites.setdefault(e.citation.source_id, e.citation)
            claims.append(Claim(text=a.trail.reasoning, citations=list(cites.values())))
        return AlertDraft(
            id=alert_id(kind.value, a.manager_id, [a.invoice_id]), kind=kind,
            manager_id=a.manager_id, invoice_ids=[a.invoice_id],
            amount=owed,
            headline=f"{a.invoice_id} ({line.vendor_id}): {state} "
                     f"({a.finding.cause.value.replace('_', ' ')})",
            claims=claims, recommended_action=ACTION_FOR_KIND[kind])

    def verify(self, s: WorkflowState) -> dict:
        """New drafts -> verifier -> alert_raised / alert_rejected. An open alert whose evidence
        changed is re-verified and refreshed; one that no longer holds is withdrawn, with why."""
        book = WorkflowBook.load(self.d.rails)
        raised, refreshed, rejected, notes = [], [], [], []
        for draft in s.drafts:
            old = book.alerts.get(draft.id) if draft.id in book.open_ids else None
            if old and old.draft == draft:
                notes.append(f"{draft.id}: still holds, unchanged")
                continue
            verdict = self.d.verifier.verify(draft, self._verify_evidence(draft, s.now))
            ok = verdict.status == VerdictStatus.approved
            payload = {"draft": draft.model_dump(mode="json"),
                       "verdict": verdict.model_dump(mode="json"),
                       "state": "open", "withdrawn_reason": None}
            self.d.rails.append_event(wf_event("alert_raised" if ok else "alert_rejected",
                                               draft.id, s.event, payload, draft.manager_id,
                                               next(iter(draft.invoice_ids), None)))
            target = rejected if not ok else refreshed if old else raised
            target.append(draft.id)
            notes.append(f"{draft.id}: verifier {verdict.status.value}"
                         + (" (refreshed with new evidence)" if old and ok else ""))
        withdrawn = self._withdraw_stale(s, book, {d.id for d in s.drafts})
        notes += [f"{aid}: withdrawn ({why})" for aid, why in withdrawn]
        return {"raised": raised, "refreshed": refreshed, "rejected": rejected,
                "withdrawn": [aid for aid, _ in withdrawn], "trace": notes}

    def _withdraw_stale(self, s: WorkflowState, book: WorkflowBook,
                        still: set[str]) -> list[tuple[str, str]]:
        """Open item alerts on an invoice we just reassessed that the new facts don't support."""
        out = []
        for a in s.assessments:
            if a.status == "skipped":
                continue
            for al in book.open_alerts(a.manager_id):
                if al.draft.invoice_ids != [a.invoice_id] or al.draft.id in still \
                        or al.draft.kind not in ITEM_KINDS:
                    continue
                why = self._why_not(a)
                self.d.rails.append_event(wf_event(
                    "alert_withdrawn", al.draft.id, s.event,
                    {"alert_id": al.draft.id, "reason": why, "invoice_id": a.invoice_id},
                    a.manager_id, a.invoice_id))
                out.append((al.draft.id, why))
        return out

    @staticmethod
    def _why_not(a: Assessment) -> str:
        if a.finding:
            return f"now {a.finding.cause.value.replace('_', ' ')}: {a.finding.explanation}"
        if a.status == "paid":
            return "payment is now proven by bank or vendor records, on time under the terms"
        if a.status == "not_due":
            return f"not due yet under the current facts ({a.note})"
        return a.note or a.status

    # -- chase: approved-template reminder, gated by policy --------------------------------
    def chase(self, s: WorkflowState) -> dict:
        """Remind on newly raised unexplained items; on a scan, follow up on every open one.
        Level and timing come from the approved schedule; the policy gate has the last word."""
        book = WorkflowBook.load(self.d.rails)
        wanted = set(s.raised)
        if s.route == Route.scan:
            wanted |= {a.draft.id for a in book.open_alerts(s.manager_id)}
        by_mgr: dict[str, list] = {}
        for aid in sorted(wanted):
            a = book.alerts[aid]
            if aid in book.open_ids and a.draft.kind == AlertKind.unexplained_unpaid \
                    and a.draft.amount > 0:
                by_mgr.setdefault(a.draft.manager_id, []).append(a)
        sent, notes = [], []
        for mgr, alerts in sorted(by_mgr.items()):
            prior = book.chases.get(mgr, [])
            msg = self.d.agents.draft_chase(mgr, alerts, prior)
            last = prior[-1].sent_at.date() if prior and prior[-1].sent_at else None
            ok, why = self.d.rails.policy_check("send_chase", "chaser", {
                "template_id": msg.template_id, "prior_levels": [p.level for p in prior],
                "days_since_last": (s.now - last).days if last else None})
            if not ok and ("off schedule" in why or "already sent" in why):
                notes.append(f"{mgr}: next reminder not due yet ({why})")
                continue
            key = f"{mgr}|{msg.template_id}|{','.join(sorted(msg.invoice_ids))}"
            msg = msg.model_copy(update={"id": f"chase-{digest(key, s.event.id)}",
                                         "sent_at": s.event.learned_at if ok else None})
            self.d.rails.append_event(wf_event(
                "chase_sent" if ok else "chase_held", key, s.event,
                {"chase": msg.model_dump(mode="json"), "policy": why}, mgr))
            sent.append(msg.id)
            notes.append(f"{mgr}: {msg.level} reminder {'sent' if ok else 'held: ' + why}")
        return {"chases": sent, "trace": notes}

    # -- reply: read it (proof only from bank/vendor records), then reassess ---------------
    def read_reply(self, s: WorkflowState) -> dict:
        p = s.event.payload
        docs = self._evidence(s.manager_id, s.invoice_ids, s.now)
        reply = self.d.agents.read_reply(p.get("text", ""), s.manager_id, docs)
        if not reply.claims_paid:
            verdict = "reply does not claim payment"
        elif reply.proof:
            verdict = "payment claim backed by " + ", ".join(c.source_id for c in reply.proof)
        else:
            verdict = "holding position: reply claims payment with no vendor or bank proof"
        self.d.rails.append_event(wf_event(
            "reply_read", s.event.id, s.event,
            {"reply": reply.model_dump(mode="json", exclude={"received_at"}), "verdict": verdict},
            s.manager_id, (reply.invoice_ids or [None])[0]))
        ids = sorted(set(s.invoice_ids) | set(reply.invoice_ids))
        return {"invoice_ids": ids, "trace": [f"reply read: {verdict}"]}

    # -- funding review ("the moment"): case agent proposes, controller decides ----------
    def funding_review(self, s: WorkflowState) -> dict:
        """Oakline is about to fund the manager again: recommend holding what's still owed."""
        book = WorkflowBook.load(self.d.rails)
        amount = float(s.event.payload.get("amount", 0))
        alerts = book.open_alerts(s.manager_id)
        case = self.d.agents.build_case(s.manager_id, alerts)
        hold = round(min(case.proposed_hold, amount) if amount else case.proposed_hold, 2)
        case = case.model_copy(update={"proposed_hold": hold, "proposed_by": "case_agent"})
        ok, why = self.d.rails.policy_check("propose_hold", "case_agent", {})
        if not ok:
            return {"trace": [f"case not proposed: {why}"]}
        chased = len(book.chases.get(s.manager_id, []))
        rec = (f"Recommend holding ${hold:,.2f} of the ${amount:,.2f} funding" if hold
               else f"No hold recommended; release the ${amount:,.2f} funding")
        approval = self.d.approvals.request(case) if hold else None
        self.d.rails.append_event(wf_event("case_proposed", case.id, s.event, {
            "case": case.model_dump(mode="json"), "approval_id": approval,
            "funding_amount": amount, "times_chased": chased,
            "recommendation": f"{rec}. {case.summary} Chased {chased} time(s)."}, s.manager_id))
        return {"case_id": case.id,
                "trace": [f"{rec} (case {case.id}, approval {approval or 'not needed'})"]}

    # -- agreement: agent extracts terms, a controller confirms (LangGraph interrupt) ------
    def extract_terms(self, s: WorkflowState) -> dict:
        doc = Document(**s.event.payload)
        terms = self.d.agents.extract_terms(doc)
        self.d.rails.append_event(wf_event("terms_proposed", doc.id, s.event,
                                           {"terms": terms.model_dump(mode="json")}, s.manager_id))
        return {"terms": terms, "trace": [f"terms proposed for {s.manager_id}: "
                                          + ", ".join(f"{k}={v}" for k, v in _term_values(terms).items())]}

    def confirm_terms(self, s: WorkflowState) -> dict:
        """Pause until a controller confirms. Identical to what's already confirmed -> no pause."""
        vals = _term_values(s.terms)
        current = current_terms(self.d.rails.events(s.manager_id)).get(s.manager_id) or {}
        if current and all(current.get(k) == v for k, v in vals.items()):
            return {"trace": ["matches the confirmed terms; nothing to confirm"]}
        decision = interrupt({"manager_id": s.manager_id, "proposed": vals, "current": current,
                              "clauses": {k: getattr(s.terms, k).citation.locator
                                          for k in vals if getattr(s.terms, k)}})
        by, approve = decision.get("by", ""), bool(decision.get("approve"))
        ok, why = self.d.rails.policy_check("approve", by, {"proposed_by": "agreement_agent"})
        if not approve or not ok:
            return {"trace": [f"terms not confirmed ({why if approve else 'declined by ' + by})"]}
        final = {**vals, **{k: v for k, v in (decision.get("edits") or {}).items() if k in vals}}
        prev = [e for e in self.d.rails.events(s.manager_id) if e.type == "terms_confirmed"]
        ev = Event(id=f"ev-terms-confirmed-{s.manager_id}-{digest(str(sorted(final.items())), s.event.id)}",
                   type="terms_confirmed", manager_id=s.manager_id, valid_from=s.event.valid_from,
                   learned_at=s.event.learned_at, supersedes=prev[-1].id if prev else None,
                   payload={"manager_id": s.manager_id, **final, "confirmed_by": by},
                   source=s.terms.vendor_payment_days.citation)
        self.d.rails.append_event(ev)
        ids = sorted(i for i in self.d.rails.dependents_of(ev.id) if i.startswith("INV-"))
        return {"invoice_ids": ids,
                "trace": [f"terms confirmed by {by}; rechecking {len(ids)} invoice(s)"]}

    # -- bank-detail change (scenario 7): hold, verify identity, controller decides --------
    def bank_change(self, s: WorkflowState) -> dict:
        """A vendor asks to change where it gets paid. Never applied automatically: the payment
        is held, identity must be verified through a known contact, and a controller approves.
        No step here (or anywhere) can change bank details or move money."""
        e, p = s.event, s.event.payload
        doc = Document(**p) if e.type == "document_ingested" else Document(
            id=e.id, kind=SourceKind.judge, dated=e.valid_from.date(),
            text=f"Vendor {p.get('vendor_id')} asked to change its bank details.",
            data={"invoice_id": e.invoice_id, "vendor_id": p.get("vendor_id")})
        vendor = doc.vendor_id or doc.data.get("vendor_id")
        iid = doc.data.get("invoice_id") or e.invoice_id
        line = self._line(iid) if iid else None
        manager = doc.manager_id or (line.manager_id if line else None) or s.manager_id
        _, why = self.d.rails.policy_check("request_bank_change", "workflow", {})
        at_stake = round(line.amount - line.paid_amount, 2) if line else 0.0
        claim = Claim(text=f"Vendor {vendor} asked to change its bank details"
                           + (f" before a ${at_stake:,.2f} payment on {iid}." if line else "."),
                      citations=[Citation(source_id=doc.id, kind=doc.kind)])
        draft = AlertDraft(
            id=alert_id(AlertKind.bank_detail_change.value, manager or "?", [iid or vendor]),
            kind=AlertKind.bank_detail_change, manager_id=manager or "", invoice_ids=[iid] if iid else [],
            amount=at_stake, headline=f"Bank-detail change request from {vendor}: payment held",
            claims=[claim], recommended_action=(
                "Hold the payment. Verify the request through the vendor's known contact "
                "(not the details in the request), then a controller approves or declines."))
        verdict = self.d.verifier.verify(draft, [doc])
        self.d.rails.append_event(wf_event("alert_raised", draft.id, e, {
            "draft": draft.model_dump(mode="json"), "verdict": verdict.model_dump(mode="json"),
            "state": "open", "withdrawn_reason": None}, manager, iid))
        case = Case(id=f"case-bankchange-{digest(draft.id)}", manager_id=manager or "",
                    alerts=[draft.id], proposed_hold=at_stake, evidence=claim.citations,
                    summary=f"{vendor} asked to change bank details. Hold "
                            f"${at_stake:,.2f} until identity is verified and a controller "
                            "approves the change.")
        approval = self.d.approvals.request(case)
        self.d.rails.append_event(wf_event("bank_change_held", draft.id, e, {
            "vendor_id": vendor, "invoice_id": iid, "held_amount": at_stake, "policy": why,
            "approval_id": approval, "requires": ["identity_verification", "controller_approval"]},
            manager, iid))
        return {"raised": [draft.id], "case_id": case.id,
                "trace": [f"bank-detail change from {vendor}: {why}; "
                          f"${at_stake:,.2f} held pending approval {approval}"]}

    def _line(self, invoice_id: str) -> LedgerLine | None:
        for d in self.d.rails.documents():
            if d.kind == SourceKind.vendor_invoice and d.data.get("invoice_id") == invoice_id:
                return next((l for l in self.d.rails.ledger(d.manager_id)
                             if l.invoice_id == invoice_id), None)
        return None

    # -- not built yet ----------------------------------------------------------------------
    def pending(self, s: WorkflowState) -> dict:
        return {"trace": [f"{s.route.value}: step not implemented yet, event recorded only"]}

    # -- finish: mark the event processed (replay guard) ----------------------------------
    def finish(self, s: WorkflowState) -> dict:
        self.d.rails.append_event(done_event(s.event, {
            "route": s.route.value if s.route else None, "raised": s.raised,
            "rejected": s.rejected, "chases": s.chases}))
        return {"trace": ["done"]}
