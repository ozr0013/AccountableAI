"""One demo session for the dashboard: the demo world replayed through the real workflow.

Rules-mode agents (no API key, no cost) and the verifier's code checks, on in-memory rails, the
same stack `python -m rails.console.moment` runs. The dashboard only reads `snapshot()`; every
change goes through the real Judge Console, workflow, approvals and policy gate.

    from dashboard_feed import DemoSession     (see serve.py / export.py for the path setup)
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date, datetime

from contracts.models import AlertKind, Event, SourceKind, Verdict, VerdictStatus

log = logging.getLogger(__name__)

MANAGER = "keyway"
FUNDING = 18_400.0
CONTROLLER = "controller.dana"
TRUST = {   # source-trust order (rails/trust/rules.py): who wins when sources disagree
    "manager_bank": 1, "vendor_confirmation": 2, "vendor_notice": 2, "funding": 3,
    "agreement": 3, "manager_statement": 4, "manager_reply": 5,
}
LANES = ["funding", "vendor_invoice", "manager_statement", "manager_bank", "vendor_confirmation",
         "vendor_notice", "manager_reply", "judge"]
AGENT_OF_STEP = {"alert_raised": "verifier", "alert_rejected": "verifier",
                 "alert_withdrawn": "recheck", "chase_sent": "chaser", "chase_held": "chaser",
                 "case_proposed": "case_agent", "terms_proposed": "agreement_agent",
                 "bank_change_held": "workflow", "reply_read": "chaser",
                 "approval_requested": "case_agent", "approval_decided": "controller",
                 "terms_confirmed": "controller", "fact_corrected": "judge",
                 "funding_instruction": "controller", "bank_change_decided": "controller",
                 "chase_reply": "keyway"}


class _RulesVerifier:
    """The verifier's first stage (agents/verifier/rules.py): citations exist, quotes match,
    no accusatory wording. Free; the live model stage is `VERIFIER=real`."""

    def verify(self, draft, evidence):
        from agents.verifier import rules
        r = rules.check(draft, evidence)
        return Verdict(alert_id=draft.id,
                       status=VerdictStatus.approved if r.ok else VerdictStatus.blocked,
                       reasons=r.reasons or ["citations and wording checked"])


def _live_available() -> bool:
    import live
    return live.available()


def _sec(n: int):
    from datetime import timedelta
    return timedelta(seconds=n)


def _claims_paid(text: str) -> bool:
    from agents.chaser.rules import claims_paid
    return claims_paid(text)


AGENT_NAME = {"verifier": "Verifier", "recheck": "Recheck", "chaser": "Chaser agent",
              "case_agent": "Case agent", "agreement_agent": "Agreement agent",
              "workflow": "Workflow", "controller": "Controller", "judge": "Judge",
              "keyway": "Keyway", "trail": "Payment-trail agent", "cause": "Delay-cause agent",
              "policy": "Policy gate", "pattern": "Pattern check"}


def _responses(events: list[Event], trace: list[str], names: dict,
               focus: set[str] | None = None) -> list[dict]:
    """What each agent did in response to an action, in order: from the events it wrote, plus
    the trace lines that leave no event (a reminder not due yet, an alert that still holds)."""
    import re
    out = []
    for a in reversed(_audit(events)):
        if a["type"] == "approval_requested":
            continue
        what = a["what"]
        if a["type"] == "alert_raised":
            what = f"passed: {what}"
        elif a["type"] == "alert_rejected":
            what = f"blocked: {what}"
        out.append({"agent": AGENT_NAME.get(a["actor"], a["actor"]), "what": what,
                    "type": a["type"], "invoice_id": a["invoice_id"]})
    for e in events:
        if e.type == "document_ingested" and str(e.payload.get("id", "")).startswith("ev-judge-"):
            out.insert(0, {"agent": "Judge", "type": "document",
                           "what": f"new record: {e.payload.get('text', '')}", "invoice_id": e.invoice_id})
    seen = set()
    for line in trace:
        m = re.match(r"(INV-[\w-]+): (\w+)(?:, cause (\w+))?(?: \((.*)\))?$", line)
        if m and m.group(1) not in seen and (focus is None or m.group(1) in focus):
            seen.add(m.group(1))
            st = m.group(2).replace("_", " ")
            what = f"{m.group(1)}: {st}" + (f", cause {m.group(3).replace('_', ' ')}" if m.group(3) else "") \
                + (f" ({m.group(4)})" if m.group(4) else "")
            out.append({"agent": "Delay-cause agent", "type": "assess", "what": what, "invoice_id": m.group(1)})
        elif m:
            continue
        elif line.startswith(("next reminder", "keyway: next reminder")) or (
                "reminder" in line and "not due yet" in line):
            out.append({"agent": "Chaser agent", "type": "chase_scheduled", "what": line.split(": ", 1)[-1],
                        "invoice_id": None})
        elif line.startswith("recheck:"):
            out.append({"agent": "Recheck", "type": "recheck", "what": line.removeprefix("recheck: "),
                        "invoice_id": None})
    held = sum(1 for line in trace if line.endswith("still holds, unchanged"))
    if held:
        out.append({"agent": "Verifier", "type": "verified", "invoice_id": None,
                    "what": f"{held} open alert{'s' * (held != 1)} re-checked against the new evidence: still hold"})
    for r in out:
        r["what"] = re.sub(r"\bv-[a-z0-9-]+\b", lambda m: names.get(m.group(0), m.group(0)), r["what"])
    return out


def _iso(x) -> str | None:
    if x is None:
        return None
    return x.isoformat() if isinstance(x, (date, datetime)) else str(x)


class DemoSession:
    def __init__(self, seed: int = 13, n_clean: int = 100, budget: float = 1.0):
        from live import Meter
        self.seed, self.n_clean = seed, n_clean
        self.meter = Meter(budget)
        self.live = False
        self.build()

    # -- setup ----------------------------------------------------------------------------
    def build(self) -> None:
        from agents import RealAgents
        from workflow import Deps, Workflow
        from workflow.approvals import RailsApprovals
        from workflow.memrails import MemoryRails
        from workflow.run import funding_event, scan_events
        from world import generate
        from world.generator import ALL_SCENARIOS

        logging.getLogger("langgraph").setLevel(logging.ERROR)
        self.world = generate(self.seed, ALL_SCENARIOS, self.n_clean)
        self.rails = MemoryRails()
        # approvals are stamped on the demo clock, not the wall clock
        self.approvals = RailsApprovals(self.rails, clock=lambda: self._clock(23, 50))
        self.rules_agents, self.rules_verifier = RealAgents(None), _RulesVerifier()
        self.deps = Deps(self.rails, self.rules_agents, self.rules_verifier, self.approvals)
        self.wf = Workflow(self.deps)
        self.wf.replay(self.world.events() + scan_events([MANAGER], self.world.as_of))
        self.wf.replay([funding_event(MANAGER, FUNDING, self.world.as_of)])
        self.history: list[dict] = []          # every action, oldest first
        self.actions = 0
        if self.live:
            self.set_live(True)

    def reset(self) -> None:
        self.build()

    def _clock(self, h: int = 12, m: int = 0, s: int = 0) -> datetime:
        from datetime import UTC, time
        return datetime.combine(self.rails.now(), time(h, m, s), tzinfo=UTC)

    def set_live(self, on: bool) -> dict:
        """Live: the next events go to Claude (Sonnet agents, verifier model stage), metered."""
        import live

        from agents import RealAgents
        from workflow.names import names_from
        if on and not live.available():
            return {"ok": False, "error": "no ANTHROPIC_API_KEY in the feed's environment"}
        from workflow import Deps, Workflow
        if on:
            agents = RealAgents(live.MeteredLLM(self.meter))
            agents.set_names(names_from(self.rails))
            verifier = live.MeteredVerifier(self.meter, self.rules_verifier)
        else:
            agents, verifier = self.rules_agents, self.rules_verifier
        # Deps is frozen; rebind the graph while retaining paused/checkpointed runs.
        deps = Deps(self.rails, agents, verifier, self.approvals)
        workflow = Workflow(deps, checkpointer=self.wf.graph.checkpointer)
        self.deps, self.wf, self.live = deps, workflow, on
        return {"ok": True}

    # -- the loop: every action runs the workflow, then the follow-ups ----------------------
    def _act(self, kind: str, summary: str, events: list[Event], affected: list[str],
             before: dict, appended: bool = False) -> dict:
        """Handle the action's events, then let the other agents respond: a scan (chaser
        follows up on schedule, pattern and causes refresh) and, when what Keyway owes changed,
        a fresh case-agent review of the pending funding. Returns what each agent did."""
        from workflow.run import funding_event
        seq0 = before["meta"]["events"]
        spent0 = self.meter.spent
        runs = [self.wf.handle(e) if appended else self.wf.ingest(e) for e in events]
        self.actions += 1
        n, t = self.actions, (events[-1].learned_at if events else self._clock())
        scan = Event(id=f"ev-scan-{MANAGER}-followup-{n}", type="scan_requested", manager_id=MANAGER,
                     valid_from=t, learned_at=t + _sec(1),
                     payload={"as_of": self.rails.now().isoformat()})
        runs += self.wf.replay([scan])
        if self._unexplained() != before["headline"]["unexplained"]:
            ev = funding_event(MANAGER, FUNDING, self.rails.now())
            runs += self.wf.replay([ev.model_copy(update={
                "id": f"{ev.id}-recheck-{n}", "valid_from": t, "learned_at": t + _sec(2)})])
        after = self.snapshot()
        trace = [line for r in runs for line in r.trace]
        out = {"ok": True, "kind": kind, "summary": summary, "affected": affected,
               "events": [e.model_dump(mode="json") for e in events], "trace": trace,
               "diff": (d := diff(before, after, affected)),
               "responses": _responses(self.rails.events()[seq0:], trace, after["meta"]["names"],
                                       set(affected) | {c["id"] for c in d["changed"]}),
               "cost": round(self.meter.spent - spent0, 4), "live": self.live,
               "state": after}
        self.history.append({k: out[k] for k in ("kind", "summary", "affected", "diff", "responses", "cost")})
        out["state"]["history"] = self.history
        return out

    def _unexplained(self) -> float:
        from workflow.book import WorkflowBook
        b = WorkflowBook.load(self.rails)
        return round(sum(a.draft.amount for a in b.open_alerts(MANAGER)
                         if a.draft.kind == AlertKind.unexplained_unpaid and a.draft.amount > 0), 2)

    # -- actions --------------------------------------------------------------------------
    def console(self, preset: str | None = None, params: dict | None = None,
                text: str | None = None) -> dict:
        """Judge Console: one fact in, selective recheck, and what changed vs what didn't."""
        from rails.console import ConsoleError, JudgeConsole
        before = self.snapshot()
        jc = JudgeConsole(self.rails)
        try:
            res = jc.apply_text(text, MANAGER) if text else jc.apply_preset(preset or "", params)
        except ConsoleError as e:
            return {"ok": False, "error": str(e), "suggestions": e.suggestions}
        return self._act("fact", res.summary, res.events, res.affected, before, appended=True)

    def reply(self, invoice_id: str, text: str, proof: str = "none") -> dict:
        """The judge plays Keyway and answers a reminder. Proof, if attached, is the record it
        would actually send: a bank payment line or the vendor's confirmation."""
        import hashlib

        from rails.console import ConsoleError, JudgeConsole
        before = self.snapshot()
        jc = JudgeConsole(self.rails)
        text = (text or "").strip()
        if proof not in ("none", "bank", "vendor"):
            return {"ok": False, "error": "proof must be none, bank, or vendor"}
        if not text:
            return {"ok": False, "error": "write Keyway's reply first"}
        try:
            jc.invoice(invoice_id)
        except ConsoleError as e:
            return {"ok": False, "error": str(e), "suggestions": e.suggestions}
        events: list[Event] = []
        if proof in ("bank", "vendor"):
            preset = "bank_shows_payment" if proof == "bank" else "vendor_confirms_paid"
            key = "on" if proof == "bank" else "paid_on"
            res = jc.apply_preset(preset, {"invoice_id": invoice_id, key: self.rails.now().isoformat()})
            events += res.events
        now = self._clock(12, 30)
        eid = "ev-keyway-reply-" + hashlib.sha1(f"{invoice_id}|{text}|{proof}|{self.actions}".encode()).hexdigest()[:10]
        ev = Event(id=eid, type="chase_reply", manager_id=MANAGER, invoice_id=invoice_id,
                   valid_from=now, learned_at=now,
                   payload={"id": eid, "manager_id": MANAGER, "invoice_ids": [invoice_id], "text": text,
                            "received_at": now.isoformat(), "claims_paid": _claims_paid(text), "proof": []},
                   source={"source_id": eid, "kind": SourceKind.manager_reply})
        self.rails.append_event(ev)
        events.append(ev)
        attached = {"bank": " with a bank record attached", "vendor": " with the vendor's confirmation attached"}
        return self._act("reply", f"Keyway replied on {invoice_id}{attached.get(proof, ', no attachment')}: “{text}”",
                         events, [invoice_id], before, appended=True)

    def advance(self, days: int) -> dict:
        """Move the demo clock forward: due dates pass, the chaser's schedule moves on."""
        from datetime import UTC, time, timedelta
        days = max(1, min(int(days), 30))
        before = self.snapshot()
        new = self.rails.now() + timedelta(days=days)
        at = datetime.combine(new, time(8), tzinfo=UTC)
        meta = next(e for e in self.rails.events() if e.type == "world_meta")
        ev = meta.model_copy(update={"id": f"ev-world-clock-{new.isoformat()}", "valid_from": at,
                                     "learned_at": at, "payload": {**meta.payload, "as_of": new.isoformat()}})
        return self._act("clock", f"Demo clock moved {days} day{'s' * (days != 1)} to {new:%b} {new.day}.",
                         [ev], [], before)

    def decide(self, approval_id: str, approve: bool, by: str = CONTROLLER,
               identity_verified: bool = False) -> dict:
        """Controller decision. The rails policy gate refuses agents and self-approval; a vendor
        bank-detail change also needs identity verified before the gate lets it through."""
        before = self.snapshot()
        case = next((c for c in self._cases(self.rails.events()) if c["approval_id"] == approval_id), None)
        if case is None:
            return {"ok": False, "error": f"no approval {approval_id}", "state": before}
        if case["status"] == "superseded":
            return {"ok": False, "error": "superseded by a newer review", "state": before}
        # Check every decision before appending an irreversible approval event.
        ok, why = self.rails.policy_check("approve", by, {"proposed_by": case["proposed_by"]})
        if not ok:
            return {"ok": False, "error": why, "state": before}
        if case["kind"] == "bank_change" and approve and not identity_verified:
            return {"ok": False, "error": "Verify the vendor's identity before approving bank details.",
                    "state": before}
        try:
            self.approvals.decide(approval_id, by, approve)
        except (PermissionError, ValueError, KeyError) as e:
            return {"ok": False, "error": str(e), "state": before}
        t = self._clock(23, 55)
        if case["kind"] == "funding":
            hold = case["proposed_hold"] if approve else 0.0
            release = round((case["funding_amount"] or FUNDING) - hold, 2)
            summary = (f"Funding instruction recorded: send ${release:,.2f} to Keyway now"
                       + (f", hold ${hold:,.2f} until payment proof." if hold else " (hold declined)."))
            payload = {"approval_id": approval_id, "release": release, "hold": hold,
                       "approved": approve, "by": by, "note": "a recorded instruction for a person; no money moved"}
            ev = Event(id=f"ev-funding-instruction-{approval_id}", type="funding_instruction",
                       manager_id=MANAGER, valid_from=t, learned_at=t, payload=payload)
        else:
            vendor = next((d.vendor_id for d in self.rails.documents(MANAGER)
                           if d.id in {c["source_id"] for c in case["evidence"]}), None)
            ok, why = self.rails.policy_check("update_bank_details", by, {
                "identity_verified": identity_verified and approve, "approved_by": by if approve else None,
                "requested_by": vendor})
            summary = ("Bank-detail change approved: identity verified, new details may be used."
                       if ok else f"Bank-detail change stays held: {why}.")
            ev = Event(id=f"ev-bank-change-decided-{approval_id}", type="bank_change_decided",
                       manager_id=MANAGER, valid_from=t, learned_at=t,
                       payload={"approval_id": approval_id, "vendor_id": vendor, "allowed": ok,
                                "reason": why, "identity_verified": identity_verified, "by": by})
        return self._act("decision", summary, [ev], [], before)

    def brief(self, question: str, use_model: bool = False) -> dict:
        from agents.briefing import Briefer
        b = Briefer(self.rails, use_model=use_model and self.meter.ok())
        out = b.brief(MANAGER, question)
        m = b.last or {}
        if m.get("mode") == "model" and "input_tokens" in m:
            self.meter.add("Briefing", m.get("model", "claude-sonnet-5-5"), m["input_tokens"], m.get("output_tokens", 0))
        return {**out.model_dump(mode="json"), "meta": m}

    # -- read model -----------------------------------------------------------------------
    def snapshot(self) -> dict:
        from rails import aging as _aging
        from rails.facts import current_terms
        from workflow.book import WorkflowBook
        from workflow.names import names_from
        from workflow.steps import Steps

        r, now = self.rails, self.rails.now()
        name = names_from(r)
        book = WorkflowBook.load(r)
        events = r.events()
        current = r._current()
        vendors = {e.payload["id"]: e.payload for e in events
                   if e.type == "entity_registered" and e.payload.get("kind") == "vendor"}
        docs = {d.id: d for d in r.documents(MANAGER)}
        from workflow import Deps
        # Rendering a read model must never invoke paid agents.
        steps = Steps(Deps(r, self.rules_agents, self.rules_verifier, self.approvals))

        alerts = [_alert(a, a.draft.id in book.open_ids) for a in book.alerts.values()]
        open_by_item: dict[str, list[str]] = {}
        for a in alerts:
            if a["open"]:
                for i in a["invoice_ids"]:
                    open_by_item.setdefault(i, []).append(a["id"])

        items = []
        for line in r.ledger(MANAGER):
            owed = round(line.amount - line.paid_amount, 2)
            relevant = owed > 0 or line.invoice_id in open_by_item or line.duplicate_of
            item = {
                "id": line.invoice_id, "vendor_id": line.vendor_id, "vendor": name(line.vendor_id),
                "trade": vendors.get(line.vendor_id, {}).get("trade"),
                "amount": line.amount, "funded": line.funded_amount, "paid": line.paid_amount,
                "owed": owed, "received": _iso(line.invoice_received),
                "funded_on": _iso(line.funded_on), "paid_on": _iso(line.paid_on),
                "due": _iso(line.due_date), "status": line.status.value,
                "duplicate_of": line.duplicate_of, "alerts": open_by_item.get(line.invoice_id, []),
                "state": "settled", "cause": None, "days_late": 0,
            }
            if relevant:
                a = steps._assess_one(line, now, explain=line.invoice_id in open_by_item)
                item["state"] = a.status
                item["days_late"] = a.days_late or 0
                item["note"] = a.note
                if a.finding:
                    item["cause"] = a.finding.cause.value
                    item["cause_text"] = a.finding.explanation
                    item["cause_confidence"] = a.finding.confidence
                    item["cause_citations"] = [c.model_dump(mode="json") for c in a.finding.citations]
                if a.trail:
                    item["trail"] = {
                        "conflict": a.trail.conflict, "winning_claim": a.trail.winning_claim,
                        "reasoning": a.trail.reasoning,
                        "winning": [c.source_id for c in a.trail.winning_sources],
                        "entries": [{"on": _iso(e.on), "kind": e.kind.value, "claim": e.claim,
                                     "source_id": e.citation.source_id,
                                     "locator": e.citation.locator, "quote": e.citation.quote}
                                    for e in a.trail.entries]}
            pattern_only = all(x.startswith("al-recurring_pattern") for x in item["alerts"])
            item["attention"] = bool(owed > 0 or line.duplicate_of or not pattern_only)
            item["paid_late"] = bool(owed <= 0 and line.paid_on and line.due_date
                                     and line.paid_on > line.due_date)
            items.append(item)

        open_alerts = [a for a in alerts if a["open"]]
        unexplained = {a["invoice_ids"][0]: a["amount"] for a in open_alerts
                       if a["kind"] == AlertKind.unexplained_unpaid.value and a["amount"] > 0}
        chases = [_chase(e) for e in events if e.type in ("chase_sent", "chase_held")]
        replies = [{"id": d.id, "on": _iso(d.dated), "text": d.text,
                    "invoice_id": d.data.get("invoice_id"), "claims_paid": d.data.get("status") == "paid"}
                   for d in docs.values() if d.kind == SourceKind.manager_reply]
        verdicts = {e.payload.get("trigger"): e.payload.get("verdict")
                    for e in events if e.type == "reply_read"}
        for rep in replies:
            # a judge reply is an event whose document id is "<event id>:<invoice id>"
            rep["judge"] = rep["id"].startswith(("ev-judge-", "ev-keyway-reply-"))
            rep["verdict"] = (verdicts.get(rep["id"].split(":")[0])
                              or verdicts.get(f"ev-doc-{rep['id']}"))

        cases = self._cases(events)
        recon = r.reconcile(MANAGER)
        terms = current_terms(current).get(MANAGER, {})
        cited = {c["source_id"] for a in alerts for cl in a["claims"] for c in cl["citations"]}
        cited |= {c["source_id"] for cs in cases for c in cs["evidence"]}
        for it in items:
            cited |= {e["source_id"] for e in it.get("trail", {}).get("entries", [])}
            cited |= {c["source_id"] for c in it.get("cause_citations", [])}
        cited |= {d.id for d in docs.values() if d.kind in (SourceKind.agreement, SourceKind.manager_reply)}

        return {
            "meta": {"as_of": _iso(now), "parent": "Oakline Homes", "manager_id": MANAGER,
                     "manager": name(MANAGER), "funding_pending": FUNDING,
                     "controller": CONTROLLER, "vendors": len(vendors),
                     "documents": len(docs), "events": len(events),
                     "agents": "live" if self.live else "rules",
                     "live_available": _live_available(), "meter": self.meter.to_json(),
                     "start": _iso(self.world.as_of), "actions": self.actions,
                     "names": {**{v: name(v) for v in vendors}, MANAGER: name(MANAGER)},
                     "generated_at": datetime.now().isoformat(timespec="seconds")},
            "headline": {
                "funded": recon["funded"], "paid": recon["paid"], "held": recon["held"],
                "owed": recon["owed"], "unfunded": recon["unfunded"],
                "unexplained": round(sum(unexplained.values()), 2),
                "unexplained_items": sorted(unexplained, key=lambda i: -unexplained[i]),
                "vendors_owed": len({next(x["vendor_id"] for x in items if x["id"] == i)
                                     for i in unexplained}),
                "times_chased": len([c for c in chases if c["sent"]
                                     and set(c["invoice_ids"]) & set(unexplained)]),
                "reminders_sent": len([c for c in chases if c["sent"]]),
                "open_alerts": len(open_alerts),
                "proposed_hold": next((c["proposed_hold"] for c in reversed(cases)
                                       if c["kind"] == "funding"), 0.0),
            },
            "recon": recon,
            "aging": _aging.aging(current, MANAGER, now),
            "late_months": r.late_months(MANAGER),
            "terms": terms,
            "items": items,
            "alerts": sorted(alerts, key=lambda a: (not a["open"], -a["amount"], a["id"])),
            "cases": cases,
            "chases": chases,
            "replies": replies,
            "agents": _agent_activity(events, items, alerts, chases, cases),
            "audit": _audit(events),
            "guardrails": self._guardrails(chases),
            "documents": {i: _doc(docs[i]) for i in sorted(cited) if i in docs},
            "history": self.history,
            "decisions": [{"type": e.type, "at": _iso(e.learned_at), **e.payload}
                          for e in events if e.type in ("funding_instruction", "bank_change_decided")],
        }

    def _cases(self, events: list[Event]) -> list[dict]:
        decided = {e.payload["approval_id"]: e for e in events if e.type == "approval_decided"}
        proposed = {e.payload["case"]["id"]: e for e in events if e.type == "case_proposed"}
        out = []
        for e in events:
            if e.type != "approval_requested":
                continue
            c = e.payload["case"]
            aid = e.payload["approval_id"]
            d = decided.get(aid)
            prop = proposed.get(c["id"])
            out.append({
                "approval_id": aid, "id": c["id"], "alerts": c["alerts"],
                "kind": "bank_change" if "bankchange" in c["id"] else "funding",
                "proposed_hold": c["proposed_hold"], "summary": c["summary"],
                "evidence": c["evidence"], "proposed_by": c["proposed_by"],
                "recommendation": prop.payload.get("recommendation") if prop else None,
                "funding_amount": prop.payload.get("funding_amount") if prop else None,
                "requested_at": _iso(e.learned_at),
                "status": ("approved" if d.payload["approved"] else "declined") if d else "pending",
                "decided_by": d.payload["approver"] if d else None,
                "decided_at": _iso(d.learned_at) if d else None,
            })
        funding = [c for c in out if c["kind"] == "funding"]
        for c in funding[:-1]:        # a later review replaced it (a fact changed)
            if c["status"] == "pending":
                c["status"] = "superseded"
        return out

    def _guardrails(self, chases: list[dict]) -> list[dict]:
        """Live calls to the rails policy gate: what an agent is not allowed to do."""
        body = (chases[-1]["body"] if chases else "Please pay the vendor today.")
        tries = [
            ("Chaser sends a free-form email to Keyway", "send_outbound", "chaser",
             {"to": "ap@keyway.example", "body": body.replace("friendly reminder", "final notice"),
              "proposed_by": "chaser"}),
            ("Case agent pays Rio Grande Plumbing", "pay_vendor", "case_agent", {}),
            ("Case agent approves its own hold", "approve", "case_agent", {"proposed_by": "case_agent"}),
            ("Case agent places the hold itself", "hold", "case_agent", {}),
            ("Workflow updates Summit Roofing's bank details", "update_bank_details", "workflow",
             {"identity_verified": False}),
            ("Alert drafted with no cited record", "create_alert", "verifier",
             {"citations": [], "text": "Keyway owes Rio Grande $1,240."}),
            ("Controller approves the case agent's hold", "approve", CONTROLLER,
             {"proposed_by": "case_agent"}),
        ]
        out = []
        for label, action, actor, payload in tries:
            ok, why = self.rails.policy_check(action, actor, payload)
            out.append({"label": label, "action": action, "actor": actor, "allowed": ok,
                        "reason": why})
        return out


# -- serializers --------------------------------------------------------------------------
def _cite(c) -> dict:
    return c.model_dump(mode="json") if hasattr(c, "model_dump") else c


def _alert(a, is_open: bool) -> dict:
    d = a.draft
    return {"id": d.id, "kind": d.kind.value, "invoice_ids": d.invoice_ids, "amount": d.amount,
            "headline": d.headline, "recommended_action": d.recommended_action,
            "claims": [{"text": cl.text, "citations": [_cite(c) for c in cl.citations]}
                       for cl in d.claims],
            "verdict": {"status": a.verdict.status.value, "reasons": a.verdict.reasons},
            "open": is_open, "state": "open" if is_open else a.state,
            "withdrawn_reason": a.withdrawn_reason}


def _chase(e: Event) -> dict:
    c = e.payload["chase"]
    return {"id": c["id"], "invoice_ids": c["invoice_ids"], "level": c["level"],
            "template_id": c["template_id"], "body": c["body"],
            "sent_at": c.get("sent_at") or _iso(e.learned_at), "sent": e.type == "chase_sent",
            "policy": e.payload.get("policy")}


def _doc(d) -> dict:
    return {"id": d.id, "kind": d.kind.value, "dated": _iso(d.dated), "vendor_id": d.vendor_id,
            "text": d.text, "data": d.data}


def _agent_activity(events, items, alerts, chases, cases) -> list[dict]:
    late_paid = [i for i in items if i["paid_late"]]
    paid = [i for i in items if i["owed"] <= 0 and not i["duplicate_of"]]
    items = [i for i in items if i["attention"]]
    trails = [i for i in items if i.get("trail")]
    conflicts = [i for i in trails if i["trail"]["conflict"]]
    causes = Counter(i["cause"] for i in items if i.get("cause"))
    verified = [a for a in alerts if a["verdict"]["status"] == "approved"]
    blocked = [a for a in alerts if a["verdict"]["status"] != "approved"]
    rejected = [e for e in events if e.type == "alert_rejected"]
    withdrawn = [a for a in alerts if a["state"] == "withdrawn"]
    return [
        {"agent": "Matcher", "kind": "code", "done": f"{len(paid)} invoices matched paid",
         "flagged": f"{sum(1 for i in items if i['duplicate_of'])} duplicate"},
        {"agent": "Agreement agent", "kind": "agent", "done": "3 terms confirmed (7.2, 7.4, 8.1)",
         "flagged": None},
        {"agent": "Payment-trail agent", "kind": "agent", "done": f"{len(trails)} trails built",
         "flagged": f"{len(conflicts)} sources disagree" if conflicts else None},
        {"agent": "Delay-cause agent", "kind": "agent",
         "done": ", ".join(f"{n} {c.replace('_', ' ')}" for c, n in causes.most_common()),
         "flagged": f"{causes.get('unexplained', 0)} unexplained" if causes.get("unexplained") else None},
        {"agent": "Verifier", "kind": "agent", "done": f"{len(verified)} alerts passed",
         "flagged": f"{len(blocked) + len(rejected)} blocked" if blocked or rejected else None},
        {"agent": "Recheck", "kind": "code", "done": f"{len(withdrawn)} alerts withdrawn with reasons",
         "flagged": None},
        {"agent": "Pattern check", "kind": "code",
         "done": f"{len(late_paid)} late payments measured against clause 7.2",
         "flagged": "late in 3 of 4 months" if late_paid else None},
        {"agent": "Chaser agent", "kind": "agent",
         "done": f"{sum(c['sent'] for c in chases)} reminders sent from approved templates",
         "flagged": f"{sum(not c['sent'] for c in chases)} held" if any(not c["sent"] for c in chases) else None},
        {"agent": "Case agent", "kind": "agent", "done": f"{len(cases)} cases proposed",
         "flagged": f"{sum(c['status'] == 'pending' for c in cases)} awaiting controller"
         if any(c["status"] == "pending" for c in cases) else None},
    ]


def _audit(events: list[Event]) -> list[dict]:
    """Newest first, in log order (the log is append-only, so its order is the true order)."""
    out = []
    for seq, e in enumerate(events):
        if e.type not in AGENT_OF_STEP:
            continue
        p = e.payload
        if e.type in ("alert_raised", "alert_rejected"):
            what = p["draft"]["headline"]
        elif e.type == "alert_withdrawn":
            what = f"withdrew {p['alert_id']}: {p.get('reason')}"
        elif e.type in ("chase_sent", "chase_held"):
            c = p["chase"]
            what = f"{c['level']} reminder ({', '.join(c['invoice_ids'])})" + \
                   ("" if e.type == "chase_sent" else f" held: {p.get('policy')}")
        elif e.type == "case_proposed":
            what = p.get("recommendation") or "case proposed"
        elif e.type == "approval_requested":
            what = f"asked the controller to decide {p['approval_id']}"
        elif e.type == "approval_decided":
            what = f"{'approved' if p['approved'] else 'declined'} {p['approval_id']}"
        elif e.type == "bank_change_held":
            what = f"bank-detail change from {p.get('vendor_id')} held (${p.get('held_amount', 0):,.2f})"
        elif e.type == "fact_corrected":
            what = f"corrected {p['fact_key']} to {p['value']}"
        elif e.type == "reply_read":
            what = p.get("verdict") or "reply read"
        elif e.type == "funding_instruction":
            what = (f"funding instruction: send ${p['release']:,.2f} to Keyway"
                    + (f", hold ${p['hold']:,.2f}" if p["hold"] else "") + " (no money moved)")
        elif e.type == "bank_change_decided":
            what = ("bank-detail change approved" if p["allowed"] else f"bank-detail change still held: {p['reason']}")
        elif e.type == "chase_reply":
            what = f"replied: \u201c{p.get('text')}\u201d"
        else:
            what = e.type.replace("_", " ")
        out.append({"seq": seq, "id": e.id, "at": _iso(e.learned_at), "type": e.type,
                    "actor": AGENT_OF_STEP[e.type], "what": what,
                    "invoice_id": e.invoice_id})
    return out[::-1]


def diff(before: dict, after: dict, rechecked: list[str]) -> dict:
    """What a new fact changed, and what it left alone."""
    b_items = {i["id"]: i for i in before["items"]}
    changed, unchanged = [], []
    keys = ("state", "cause", "due", "received", "owed", "alerts")
    for it in after["items"]:
        old = b_items.get(it["id"])
        if old is None:
            continue
        delta = {k: [old.get(k), it.get(k)] for k in keys if old.get(k) != it.get(k)}
        if delta:
            changed.append({"id": it["id"], "vendor": it["vendor"], "delta": delta})
        elif it["id"] in rechecked:
            unchanged.append(it["id"])
    b_alerts = {a["id"]: a for a in before["alerts"]}
    withdrawn = [a for a in after["alerts"] if a["state"] == "withdrawn"
                 and b_alerts.get(a["id"], {}).get("open")]
    raised = [a for a in after["alerts"] if a["open"] and not b_alerts.get(a["id"], {}).get("open")]
    kept = [a for a in after["alerts"] if a["open"] and b_alerts.get(a["id"], {}).get("open")]
    return {
        "rechecked": rechecked, "changed": changed, "rechecked_unchanged": unchanged,
        "untouched": len(after["items"]) - len(changed) - len(unchanged),
        "withdrawn": withdrawn, "raised": raised, "kept": [a["id"] for a in kept],
        "unexplained": [before["headline"]["unexplained"], after["headline"]["unexplained"]],
        "unexplained_items": [before["headline"]["unexplained_items"],
                              after["headline"]["unexplained_items"]],
    }
