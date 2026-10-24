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
                 "terms_confirmed": "controller", "fact_corrected": "judge"}


class _RulesVerifier:
    """The verifier's first stage (agents/verifier/rules.py): citations exist, quotes match,
    no accusatory wording. Free; the live model stage is `VERIFIER=real`."""

    def verify(self, draft, evidence):
        from agents.verifier import rules
        r = rules.check(draft, evidence)
        return Verdict(alert_id=draft.id,
                       status=VerdictStatus.approved if r.ok else VerdictStatus.blocked,
                       reasons=r.reasons or ["citations and wording checked"])


def _iso(x) -> str | None:
    if x is None:
        return None
    return x.isoformat() if isinstance(x, (date, datetime)) else str(x)


class DemoSession:
    def __init__(self, seed: int = 13, n_clean: int = 100):
        self.seed, self.n_clean = seed, n_clean
        self.history: list[dict] = []          # Judge Console results, oldest first
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
        self.approvals = RailsApprovals(self.rails)
        self.deps = Deps(self.rails, RealAgents(None), _RulesVerifier(), self.approvals)
        self.wf = Workflow(self.deps)
        self.wf.replay(self.world.events() + scan_events([MANAGER], self.world.as_of))
        self.wf.replay([funding_event(MANAGER, FUNDING, self.world.as_of)])
        self.history = []

    def reset(self) -> None:
        self.build()

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
        runs = [self.wf.handle(e) for e in res.events]
        if self.snapshot()["headline"]["unexplained"] != before["headline"]["unexplained"]:
            # What's owed changed, so the pending funding gets a fresh case-agent review.
            from workflow.run import funding_event
            ev = funding_event(MANAGER, FUNDING, self.world.as_of)
            ev = ev.model_copy(update={"id": f"{ev.id}-recheck-{len(self.history) + 1}",
                                       "learned_at": res.events[-1].learned_at})
            runs += self.wf.replay([ev])
        after = self.snapshot()
        out = {"ok": True, "summary": res.summary, "affected": res.affected,
               "events": [e.model_dump(mode="json") for e in res.events],
               "trace": [line for r in runs for line in r.trace],
               "diff": diff(before, after, res.affected),
               "state": after}
        self.history.append({k: out[k] for k in ("summary", "affected", "diff")})
        return out

    def decide(self, approval_id: str, approve: bool, by: str = CONTROLLER) -> dict:
        """Controller decision. The rails policy gate refuses agents and self-approval."""
        case = next((c for c in self._cases(self.rails.events()) if c["approval_id"] == approval_id), None)
        if case and case["status"] == "superseded":
            return {"ok": False, "error": "superseded by a newer review", "state": self.snapshot()}
        try:
            self.approvals.decide(approval_id, by, approve)
        except (PermissionError, ValueError, KeyError) as e:
            return {"ok": False, "error": str(e), "state": self.snapshot()}
        return {"ok": True, "state": self.snapshot()}

    def brief(self, question: str, use_model: bool = False) -> dict:
        from agents.briefing import Briefer
        b = Briefer(self.rails, use_model=use_model)
        out = b.brief(MANAGER, question)
        return {**out.model_dump(mode="json"), "meta": b.last}

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
        steps = Steps(self.deps)

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
            rep["judge"] = rep["id"].startswith("ev-judge-")
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
                     "agents": "rules",
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
