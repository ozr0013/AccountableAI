"""Thin intake adapter around the existing Workflow; no second decision engine.

Workflow owns assessment, alert state, reminder scheduling, approvals and case proposals.
This adapter validates documents, advances an explicit clock, and observes workflow results.
"""
from __future__ import annotations

from datetime import date

from agents import RealAgents
from agents.intake import Intake
from agents.intake.engine import stamp
from agents.verifier.rules import check as check_draft
from contracts.models import Citation, Event, SourceKind, Verdict, VerdictStatus
from rails.audit import Audit
from rails.events.ingest import document_event
from rails.serialization import digest
from workflow.approvals import RailsApprovals
from workflow.book import WorkflowBook
from workflow.memrails import MemoryRails
from workflow.runner import Workflow
from workflow.steps import Deps


class CodeVerifier:
    """Expose the existing verifier's code checks through its existing protocol."""

    def verify(self, draft, evidence):
        checked = check_draft(draft, evidence)
        return Verdict(alert_id=draft.id,
                       status=VerdictStatus.approved if checked.ok else VerdictStatus.blocked,
                       reasons=checked.reasons or ["existing verifier code checks passed"],
                       uncited_claims=checked.uncited_claims, banned_terms=checked.banned_terms)


class _NoVerifier:
    """Evaluation-only ablation; never selected by the file-ingestion application."""

    def verify(self, draft, evidence):
        return Verdict(alert_id=draft.id, status=VerdictStatus.approved,
                       reasons=["evaluation ablation: verifier disabled"])


class LiveSystem:
    def __init__(self, public, system="ours", llm=None, cache=None, regression_dir=None,
                 rails=None, agents=None, verifier=None):
        if system not in ("rules", "ours", "no_verifier", "no_checks"):
            raise ValueError("unknown system")
        self.public, self.kind = public, system
        self.simulated = bool(public.get("simulated", False))
        self.llm = llm if system != "rules" else None
        self.rails = rails if rails is not None else MemoryRails()
        clocks = [e.learned_at.date() for e in self.rails.events()]
        self.day = max([date.fromisoformat(public["start"]), *clocks])
        self.end = date.fromisoformat(public["as_of"]) if self.simulated else None
        self.intake = Intake(public["vendors"], self.llm, checks=system != "no_checks", cache=cache,
                             earliest=date.fromisoformat(public["start"]), latest=self.end)
        self.audit = Audit(self.rails, regression_dir)
        self.approvals = RailsApprovals(self.rails, clock=lambda: stamp(self.day))
        verify = verifier or (_NoVerifier() if system == "no_verifier" else CodeVerifier())
        self.workflow = Workflow(Deps(self.rails, agents or RealAgents(None), verify, self.approvals))
        self.last_runs = []
        self._actions_read = {e.id for e in self.rails.events()
                              if e.type in ("chase_sent", "alert_raised")}
        self._restore_intake()
        # A local event-log restart reconstructs unfinished agreement interrupts through
        # Workflow.handle. Completed workflow events already have its replay guard.
        done = WorkflowBook.load(self.rails).processed
        for e in list(self.rails.events()):
            if e.type == "document_ingested" and e.id not in done:
                self._run(e)

    def _restore_intake(self):
        for e in self.rails.events():
            if e.type == "source_document_received":
                self.intake.processed.add(e.payload["document"]["id"])
            if e.type == "document_ingested":
                provenance = e.payload.get("extraction", {})
                if provenance.get("fingerprint"):
                    self.intake.seen.add(provenance["fingerprint"])
        for doc in self.rails.documents():
            if doc.kind == SourceKind.vendor_invoice and doc.data.get("invoice_id"):
                self.intake.invoices[doc.data["invoice_id"]] = {**doc.data, "vendor_id": doc.vendor_id}

    def advance(self, day):
        if day < self.day or (self.end and day > self.end):
            raise ValueError("clock must move forward and stay inside the configured demo range")
        if day != self.day:
            self.day = day
            self.audit.emit("clock_advanced", str(day), day, payload={"as_of": str(day),
                "simulated": self.simulated}, source=Citation(source_id=f"clock:{day}", kind=SourceKind.controller))

    def ingest(self, doc):
        if self.audit.halted:
            raise RuntimeError("processing stopped pending invariant repair")
        result = self.intake.process(doc, self.day)
        if not result.events:
            return result
        self.last_runs = []
        for e in result.events:
            if e.type == "terms_proposed":
                # Keep extraction provenance; agreement confirmation belongs to Workflow.
                self.rails.append_event(e.model_copy(update={"type": "intake_terms_checked"}))
                material = doc.model_copy(update={"data": {k: e.payload[k] for k in
                    ("vendor_payment_days", "holdback_pct", "approval_limit")}})
                ready = document_event(material, stamp(self.day))
                ready.payload["extraction"] = e.payload["extraction"]
                self._run(ready)
            elif e.type == "document_ingested":
                self._run(e)
            else:
                self.rails.append_event(e)
                if e.type == "extraction_disagreement":
                    self.audit.disagreement(e.invoice_id, e.payload["rules"], e.payload["model"],
                                            self.day, e.source, "extraction")
        return result

    def _run(self, event):
        self.rails.append_event(event)
        self.audit.check(self.day)  # Stop before Workflow can issue an action.
        self._settle_bank_outcomes(event)
        result = self.workflow.handle(event)
        self.last_runs.append(result)
        self._observe(event, result)
        self.audit.check(self.day)
        return result

    def _settle_bank_outcomes(self, event):
        if event.type != "document_ingested" or event.payload.get("kind") not in (
                "manager_bank", "vendor_confirmation"):
            return
        for line in self.rails.ledger(event.manager_id):
            if line.invoice_id not in self.rails.dependents_of(event.id):
                continue
            if line.paid_amount < line.amount or not line.paid_on:
                continue
            observed = {}
            previous = self.audit.predictions().get((line.invoice_id, "alert"))
            if previous and previous.payload.get("context", {}).get("alert_kind") in (None, "unexplained_unpaid"):
                observed["alert"] = False
            if line.due_date and line.paid_on <= line.due_date:
                observed["cause"] = "vendor_side"
            self.audit.settle(line.invoice_id, observed, line.paid_on, self.day, event.source)

    def _observe(self, event, result):
        if result.skipped or result.waiting:
            return
        state = self.workflow.graph.get_state(self.workflow._cfg(event.id)).values
        book = WorkflowBook.load(self.rails)
        for assessment in state.get("assessments", []):
            if isinstance(assessment, dict):
                from workflow.state import Assessment
                assessment = Assessment.model_validate(assessment)
            iid = assessment.invoice_id
            docs = self.rails.documents(event.manager_id, iid)
            invoice = next((d for d in docs if d.kind == SourceKind.vendor_invoice and
                            d.data.get("invoice_id") == iid), None)
            if not invoice:
                continue
            source = Citation(source_id=invoice.id, kind=invoice.kind)
            self.audit.emit("assessment_recorded", [event.id, iid], self.day, iid,
                            {"assessment": assessment.model_dump(mode="json"), "trigger": event.id}, source)
            if assessment.finding:
                self.audit.predict(iid, "cause", assessment.finding.cause.value, self.day,
                                   assessment.finding.citations)
            alerts = [a for a in book.open_alerts() if iid in a.draft.invoice_ids]
            unpaid = next((a for a in alerts if a.draft.kind.value == "unexplained_unpaid"), None)
            self.audit.predict(iid, "alert", unpaid is not None, self.day, [source],
                               context={"alert_kind": "unexplained_unpaid" if unpaid else None})

    def pending_terms(self):
        return {eid: self.workflow.graph.get_state(self.workflow._cfg(eid)).interrupts[0].value
                for eid in self.workflow.waiting()}

    def confirm_terms(self, event_id, actor="controller.local"):
        if event_id not in self.pending_terms():
            raise ValueError("no pending agreement confirmation with that event id")
        allowed, reason = self.rails.policy_check("approve", actor, {"proposed_by": "agreement_agent"})
        if not allowed:
            raise PermissionError(reason)
        state = self.workflow.graph.get_state(self.workflow._cfg(event_id)).values
        terms = state["terms"]
        payload = {k: getattr(terms, k).value for k in
                   ("vendor_payment_days", "holdback_pct", "approval_limit") if getattr(terms, k)}
        payload.update(manager_id=terms.manager_id, confirmed_by=actor)
        source = terms.vendor_payment_days.citation
        self.audit.emit("controller_decision", [event_id, actor], self.day,
                        payload={"decision": "approve_terms", "human_touch": True,
                                 "simulated": self.simulated, "actor": actor}, source=source)
        event = Event(id=f"terms-confirmed-{digest([event_id, payload])[:24]}", type="terms_confirmed",
                      manager_id=terms.manager_id, valid_from=stamp(self.day), learned_at=stamp(self.day),
                      payload=payload, source=source)
        return self._run(event)  # Existing Workflow settles its paused agreement thread.

    def assess(self):
        """A clock tick asks the existing workflow to scan; its code chooses all next steps."""
        manager = self.public.get("manager_id", "keyway")
        source = Citation(source_id=f"clock:{self.day}", kind=SourceKind.controller)
        return self._run(Event(id=f"scan-{manager}-{self.day}", type="scan_requested", manager_id=manager,
                               valid_from=stamp(self.day), learned_at=stamp(self.day),
                               payload={"as_of": str(self.day)}, source=source))

    def review_funding(self, amount=0):
        if amount < 0:
            raise ValueError("funding amount cannot be negative")
        manager = self.public.get("manager_id", "keyway")
        revision = [e.id for e in self.rails.events() if e.type in
                    ("document_ingested", "fact_corrected", "terms_confirmed")]
        return self._run(Event(id=f"funding-review-{digest([manager, str(self.day), amount, revision])[:24]}",
            type="funding_review_requested", manager_id=manager, valid_from=stamp(self.day),
            learned_at=stamp(self.day), payload={"amount": amount},
            source=Citation(source_id=f"funding-review:{self.day}", kind=SourceKind.controller)))

    def actions(self):
        """Simulation observer only. It reports actions already chosen by Workflow."""
        actions = []
        for e in list(self.rails.events()):
            if e.id in self._actions_read:
                continue
            if e.type == "chase_sent":
                actions.extend((iid, "reminder") for iid in e.payload["chase"]["invoice_ids"])
            elif e.type == "alert_raised" and e.payload["draft"]["kind"] == "paperwork_request":
                actions.extend((iid, "w9") for iid in e.payload["draft"]["invoice_ids"])
            self._actions_read.add(e.id)
        return actions

    def output(self):
        book = WorkflowBook.load(self.rails)
        assessments = {e.invoice_id: e.payload["assessment"] for e in self.rails.events()
                       if e.type == "assessment_recorded"}
        decisions = {}
        for line in self.rails.ledger(self.public.get("manager_id", "keyway")):
            a = assessments.get(line.invoice_id, {})
            alerts = [x for x in book.open_alerts() if line.invoice_id in x.draft.invoice_ids]
            relevant = next((x for x in alerts if x.draft.kind.value in
                            ("unexplained_unpaid", "duplicate_billing", "bank_detail_change")), None)
            decisions[line.invoice_id] = {"cause": (a.get("finding") or {}).get("cause"),
                "status": a.get("status"), "alert": relevant.draft.kind.value if relevant else None,
                "hold": max(relevant.draft.amount, 0) if relevant and relevant.draft.kind.value == "unexplained_unpaid" else 0,
                "paid": line.paid_amount, "amount": line.amount,
                "source_ids": [c["source_id"] for c in (a.get("finding") or {}).get("citations", [])]}
        events = self.rails.events()
        return {"system": self.kind, "model_mode": "live_intake" if self.llm else "rules_fallback",
                "orchestrator": "workflow.runner.Workflow", "simulated": self.simulated,
                "decisions": decisions, "ledger": [l.model_dump(mode="json") for l in self.rails.ledger(self.public.get("manager_id", "keyway"))],
                "alerts": [a.model_dump(mode="json") for a in book.open_alerts()],
                "cases": [e.payload for e in events if e.type == "case_proposed"],
                "pending_terms": self.pending_terms(), "pending_approvals": self.approvals.pending(),
                "recurring_pattern": any(a.draft.kind.value == "recurring_pattern" for a in book.open_alerts()),
                "events": [e.model_dump(mode="json") for e in events],
                "human_touches": sum(e.type in ("controller_decision", "approval_decided") for e in events),
                "automatic_actions": sum(e.type == "chase_sent" for e in events),
                "cache_requests": self.intake.requests, "cache_hits": self.intake.hits,
                "review_docs": sorted({e.source.source_id for e in events if e.type == "needs_review" and e.source}),
                "track_record": self.audit.track_record(), "autonomy": self.audit.autonomy("reminder")}
