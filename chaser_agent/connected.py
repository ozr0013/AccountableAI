"""Replayable finance coordination over the existing Rails event log.

The caller serializes mutations. Outbox rows document requested communication; only an
explicit external receipt marks delivery. No function here calls a model or sends mail.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from agents.anomaly import AnomalyAgent
from contracts.models import Event, SourceKind
from workflow.names import names_from

from .daily import _rules_by_vendor, add_days, funded_deadline, norm_name, rules_for


class _ReconcileSnapshot:
    """One locked tick's facts; discarded after reconciliation, never shared across requests."""

    def __init__(self, rails):
        self.rails = rails
        self._events = list(rails.events())
        self._ids = {event.id for event in self._events}
        self._documents = None
        self._ledgers = {}

    def events(self, manager_id=None):
        return [event for event in self._events if manager_id in (None, event.manager_id)]

    def documents(self, manager_id=None, invoice_id=None):
        if self._documents is None:
            self._documents = list(self.rails.documents())
        return [
            document
            for document in self._documents
            if manager_id in (None, document.manager_id)
            and (invoice_id is None or document.data.get("invoice_id") in (None, invoice_id))
        ]

    def ledger(self, manager):
        if manager not in self._ledgers:
            self._ledgers[manager] = self.rails.ledger(manager)
        return self._ledgers[manager]

    def now(self):
        return self.rails.now()

    def append_event(self, event):
        self.rails.append_event(event)
        if event.id not in self._ids:
            self._ids.add(event.id)
            self._events.append(event)
            if not event.type.startswith("workflow_"):
                self._documents = None
                self._ledgers.clear()


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def record(rails, invoice_id, kind, payload, on=None, manager="keyway"):
    """Append a changed projection; replaying the same latest payload is a no-op."""
    prior = [
        e
        for e in rails.events(manager)
        if e.type == kind
        and e.invoice_id == invoice_id
        and (kind != "workflow_outbox" or e.payload.get("id") == payload.get("id"))
    ]
    if prior and prior[-1].payload == payload:
        return prior[-1]
    when = on or rails.now()
    stamp = (
        datetime.combine(when, time.min, tzinfo=UTC)
        if isinstance(when, date) and not isinstance(when, datetime)
        else when
    )
    event = Event(
        id="wf-" + _digest([manager, invoice_id, kind, payload, prior[-1].id if prior else ""]),
        type=kind,
        manager_id=manager,
        invoice_id=invoice_id,
        valid_from=stamp,
        learned_at=stamp,
        payload=deepcopy(payload),
    )
    rails.append_event(event)
    # Observability only: publish an actual persisted transition, never infer mail delivery.
    if kind == 'workflow_outbox':
        from .runtime import emit
        simulated = payload.get('acknowledged_by') == 'demo_mailer'
        emit({'type': 'mail_status', 'agent': 'system', 'invoice_id': invoice_id,
              'status': payload.get('status'), 'kind': payload.get('kind'),
              'audience': payload.get('audience'), 'body': payload.get('body', '')[:1200],
              'simulated': simulated, 'outbox_id': payload.get('id')})
        if payload.get('kind') == 'escalation' and payload.get('status') == 'needs_approval':
            emit({'type': 'alert_created', 'agent': 'system', 'invoice_id': invoice_id,
                  'status': 'needs_controller', 'outbox_id': payload.get('id'),
                  'summary': payload.get('body', '')[:1200]})
    return event


def cases(rails, manager="keyway"):
    rows, history, outboxes = {}, {}, {}
    for event in rails.events(manager):
        if not event.type.startswith("workflow_"):
            continue
        iid = event.invoice_id
        history.setdefault(iid, []).append(
            {
                "id": event.id,
                "kind": event.type,
                "at": event.learned_at.isoformat(),
                "payload": deepcopy(event.payload),
            }
        )
        if event.type == "workflow_case":
            rows[iid] = deepcopy(event.payload)
        elif event.type == "workflow_outbox":
            outboxes[event.payload["id"]] = deepcopy(event.payload)
    return [
        {
            **row,
            "history": history.get(iid, []),
            "outbox": [o for o in outboxes.values() if o["invoice_id"] == iid],
        }
        for iid, row in sorted(rows.items())
    ]


def _money(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _action_rows(mirror):
    decisions = {d["action_id"]: d for d in mirror.query("SELECT * FROM chase_decision")}
    return [
        {**a, **decisions.get(a["action_id"], {})}
        for a in mirror.query("SELECT * FROM chase_action")
    ]


def _case_documents(rails, line, today):
    from rails.matcher import gather_evidence

    docs = [d for d in rails.documents(line.manager_id) if not d.dated or d.dated <= today]
    try:
        evidence = gather_evidence(docs).evidence.get(line.invoice_id)
        bank_rows = (
            {(doc_id, _digest(row)) for doc_id, row in evidence.bank_rows} if evidence else set()
        )
    except (ValueError, TypeError, KeyError, OverflowError):
        # Malformed evidence cannot authorize an inferred match; explicit rows remain reviewable.
        bank_rows = set()
    relevant = []
    for document in docs:
        data = deepcopy(document.data)
        for field in ("payments", "lines"):
            selected = []
            for row in data.get(field, []):
                matched = row.get("invoice_id") == line.invoice_id
                if field == "payments" and not row.get("invoice_id"):
                    matched = (document.id, _digest(row)) in bank_rows
                if matched:
                    selected.append(row)
            if field in data:
                data[field] = selected
        if data.get("invoice_id") == line.invoice_id or data.get("payments") or data.get("lines"):
            relevant.append(document.model_copy(update={"data": data}))
    return relevant


STATEMENT_NOTICE_AUTO = os.getenv("STATEMENT_NOTICE_AUTO", "on").lower() == "on"


def _statement_notice(want, statement, line, manager_name):
    """The anomaly note to the manager when a statement line disagrees with the approved amount,
    from the controller-approved template; queued automatically unless STATEMENT_NOTICE_AUTO=off."""
    from rails.guards.templates import render_statement_notice
    rows = [r for r in statement.data.get("lines", []) if r.get("invoice_id") == line.invoice_id]
    stated = sum((_money(r.get("amount")) or Decimal(0) for r in rows), Decimal(0))
    ref = f"dated {statement.dated:%b} {statement.dated.day}, {statement.dated.year}" if statement.dated \
        else statement.id
    body = render_statement_notice(manager_name=manager_name, statement_ref=ref, stated=f"${stated:,.2f}",
                                   invoice_ref=line.invoice_id, approved=f"${_money(line.amount):,.2f}")
    want("statement_notice", body, "queued" if STATEMENT_NOTICE_AUTO else "needs_approval",
         key=statement.id)


def _project(rails, line, rules, actions, today):
    relevant = _case_documents(rails, line, today)
    # Only relevant rows enter the evidence fingerprint, so another invoice cannot stale a case.
    source = []
    for d in relevant:
        raw = d.model_dump(mode="json")
        raw["data"] = {**raw["data"]}
        source.append(raw)
    fingerprint = _digest({"line": line.model_dump(mode="json"), "rules": rules, "sources": source})
    findings, desired = [], []

    def flag(code, message):
        if not any(f["code"] == code for f in findings):
            findings.append({"code": code, "message": message})

    def want(kind, body, status="queued", audience=None, key=""):
        desired.append(
            {
                "id": "out-" + _digest([line.manager_id, line.invoice_id, fingerprint, kind, key]),
                "invoice_id": line.invoice_id,
                "kind": kind,
                "audience": audience or "management:" + line.manager_id,
                "body": body,
                "status": status,
                "created_on": today.isoformat(),
                "fingerprint": fingerprint,
            }
        )

    active_deadlines = [
        r for r in rules if r["rule_type"] == "pay_within_days" and r["status"] == "active"
    ]
    deadline_parameters = {
        _digest(
            {
                k: r["params"].get(k, default)
                for k, default in [
                    ("days", None),
                    ("basis", "calendar"),
                    ("from", "invoice receipt"),
                    ("undisputed_only", False),
                ]
            }
        )
        for r in active_deadlines
    }
    if len(deadline_parameters) > 1:
        flag(
            "conflicting_deadlines",
            "Active agreement deadlines disagree; controller confirmation is required.",
        )
        return "awaiting_controller", findings, desired, fingerprint, False
    term = next(
        (r for r in rules if r["rule_type"] == "pay_within_days" and r["status"] == "active"), None
    )
    if (
        not rules
        or not term
        and not any(r["status"] in ("blocking", "pending_controller") for r in rules)
    ):
        flag("agreement_missing", "An active payment deadline agreement is required.")
        want("request_agreement", f"Provide the signed agreement for {line.invoice_id}.")
        return "needs_agreement", findings, desired, fingerprint, False
    if any(r["status"] in ("blocking", "pending_controller", "rejected") for r in rules):
        flag(
            "agreement_unconfirmed", "Controller must confirm the agreement before payment review."
        )
        return "awaiting_controller", findings, desired, fingerprint, False
    invoice = next(
        (
            d
            for d in relevant
            if d.kind == SourceKind.vendor_invoice and d.vendor_id == line.vendor_id
        ),
        None,
    )
    start = line.invoice_received
    basis_from = term["params"].get("from", "invoice receipt")
    if basis_from == "invoice date":
        try:
            start = (
                date.fromisoformat(str(invoice.data.get("invoice_date") or invoice.dated))
                if invoice
                else None
            )
        except ValueError:
            start = None
    elif basis_from != "invoice receipt":
        start = None
    due = (
        funded_deadline(
            add_days(start, int(term["params"]["days"]), term["params"].get("basis", "calendar")),
            line)
        if start
        else None
    )
    if due is None:
        flag(
            "invoice_date_missing",
            "The agreement deadline basis requires verified invoice date evidence.",
        )
    disputed = term["params"].get("undisputed_only") and (
        str(line.status) in ("disputed", "InvoiceStatus.disputed")
        or invoice
        and invoice.data.get("disputed") is True
    )
    if disputed:
        flag(
            "disputed_invoice",
            "Agreement applies to undisputed invoices; controller review is required.",
        )
    for rule in rules:
        if rule["rule_type"] == "amount" and rule["status"] == "active":
            params = rule["params"]
            if params.get("per") == "fixed" and _money(params.get("amount")) != _money(line.amount):
                flag(
                    "agreement_amount_mismatch",
                    "Invoice amount differs from the fixed agreement amount.",
                )
    payments = [
        (d, p)
        for d in relevant
        if d.kind == SourceKind.manager_bank
        for p in d.data.get("payments", [])
        if p.get("invoice_id") in (None, line.invoice_id)
    ]
    valid, identity_ok = [], bool(payments)
    account_rules = [
        r for r in rules if r["rule_type"] == "pay_to_account" and r["status"] == "active"
    ]
    payee_rules = [r for r in rules if r["rule_type"] == "payee" and r["status"] == "active"]
    for d, p in payments:
        trusted = True
        if not p.get("vendor_id"):
            flag("missing_payment_identity", "Bank evidence must identify the vendor.")
            trusted = False
        elif p["vendor_id"] != line.vendor_id or d.vendor_id not in (None, line.vendor_id):
            flag("wrong_vendor", "A bank payment names a different vendor.")
            trusted = False
        for rule in payee_rules:
            expected = rule["params"].get("vendor")
            if expected and not p.get("payee"):
                flag("missing_payment_identity", "Payee name required by the agreement is missing.")
                trusted = False
            elif expected and norm_name(str(p["payee"])) != norm_name(str(expected)):
                flag("wrong_payee", "Bank payee differs from the agreement.")
                trusted = False
        for rule in account_rules:
            for field in ("account", "routing", "bank"):
                expected = rule["params"].get(field)
                if expected and not p.get(field):
                    flag("missing_payment_identity", f"Bank evidence is missing agreement {field}.")
                    trusted = False
                elif (
                    expected
                    and str(p[field]).strip().casefold() != str(expected).strip().casefold()
                ):
                    flag("wrong_payment_account", f"Bank {field} differs from the agreement.")
                    trusted = False
        amount = _money(p.get("amount"))
        try:
            paid_on = date.fromisoformat(p["on"])
            if paid_on > today or (line.invoice_received and paid_on < line.invoice_received):
                raise ValueError("future payment")
        except (ValueError, TypeError, KeyError):
            flag("invalid_payment_date", "Bank payment date is missing, invalid, or future-dated.")
            trusted = False
            paid_on = None
        if amount is None or amount <= 0:
            flag("invalid_payment_amount", "Bank payment amount must be positive and finite.")
            trusted = False
        identity_ok = identity_ok and trusted
        if trusted:
            valid.append((amount, paid_on))
    paid = sum((amount for amount, _ in valid), Decimal(0))
    expected = _money(line.amount)
    ledger_paid = _money(line.paid_amount) or Decimal(0)
    fully_paid = expected is not None and paid >= expected > 0 and ledger_paid >= expected
    if payments:
        if paid < expected:
            flag(
                "partial_payment", f"Verified payment {paid} leaves {expected - paid} outstanding."
            )
            want(
                "request_remainder",
                f"Provide payment proof for the remaining {expected - paid} on {line.invoice_id}.",
            )
        if paid > expected:
            flag("overpayment", f"Verified payment {paid} exceeds approved amount {expected}.")
        if due and any(on > due for _, on in valid):
            flag("late_payment", f"Payment was made after the agreement deadline {due}.")
    statements = [d for d in relevant if d.kind == SourceKind.manager_statement]
    matches = False
    if statements:
        latest = max(statements, key=lambda d: (d.dated or date.min, d.id))
        report = AnomalyAgent().detect_anomalies(latest, [line])
        own = [f for f in report.anomalies if f.invoice_id == line.invoice_id]
        if own:
            flag(
                "statement_mismatch",
                "Financial statement amount differs from the approved invoice.",
            )
            _statement_notice(want, latest, line, names_from(rails)(line.manager_id))
        matches = line.invoice_id in report.matched and not own
        statement_rows = [
            row for row in latest.data.get("lines", []) if row.get("invoice_id") == line.invoice_id
        ]
        if any(row.get("vendor_id") not in (None, line.vendor_id) for row in statement_rows):
            flag("statement_wrong_vendor", "A financial statement row names a different vendor.")
            matches = False
        if not statement_rows or any(
            str(row.get("status", "")).casefold() != "paid" for row in statement_rows
        ):
            flag(
                "statement_payment_unconfirmed",
                "Statement must explicitly identify the invoice as paid.",
            )
            matches = False
        if valid and (latest.dated is None or latest.dated < max(on for _, on in valid)):
            flag("statement_predates_payment", "A statement dated after the payment is required.")
            matches = False
        if latest.vendor_id not in (None, line.vendor_id):
            flag("statement_wrong_vendor", "Statement names a different vendor.")
            matches = False
    if payments and not matches:
        want("request_statement", f"Provide a matching financial statement for {line.invoice_id}.")
    if payments:
        if findings:
            want(
                "review_anomaly",
                f"Review {line.invoice_id}: " + "; ".join(f["message"] for f in findings),
                "needs_approval",
                "controller",
            )
        eligible = fully_paid and matches and identity_ok and due is not None and not disputed
        if findings:
            return "needs_controller", findings, desired, fingerprint, eligible
        return (
            ("closed" if eligible else "awaiting_statement"),
            findings,
            desired,
            fingerprint,
            eligible,
        )
    vendor_claim = any(
        d.kind == SourceKind.vendor_confirmation
        and d.vendor_id == line.vendor_id
        and d.data.get("status") == "paid"
        for d in relevant
    )
    if line.paid_amount > 0 or vendor_claim:
        flag(
            "payment_proof_unverified",
            "Payment is reported by the ledger or vendor but strict bank evidence is missing.",
        )
        want(
            "request_payment_proof", f"Provide identified bank payment proof for {line.invoice_id}."
        )
        if not matches:
            want(
                "request_statement",
                f"Provide a matching financial statement for {line.invoice_id}.",
            )
        return "needs_controller", findings, desired, fingerprint, False
    if findings:
        return "needs_controller", findings, desired, fingerprint, False
    for a in actions:
        if a.get("action") not in ("reminder", "escalate") or a.get("status") not in (
            "queued",
            "approved",
            "needs_approval",
            "needs_controller",
        ):
            continue
        if a.get("due_date") and due and a["due_date"] != due.isoformat():
            continue
        if a.get("rule_id") and a["rule_id"] != term["rule_id"]:
            continue
        if a.get("amount_owed") is not None and _money(a["amount_owed"]) != expected:
            continue
        want(
            "chase_reminder" if a["action"] == "reminder" else "escalation",
            a.get("message") or a.get("reason") or "Controller review required.",
            "queued" if a["status"] in ("queued", "approved") else "needs_approval",
            key=a["action_id"],
        )
        desired[-1]["action_id"] = a["action_id"]
    if findings:
        return "needs_controller", findings, desired, fingerprint, False
    return (
        ("overdue" if due and today > due else "monitoring"),
        findings,
        desired,
        fingerprint,
        False,
    )


def reconcile(rails, mirror, manager="keyway", as_of=None):
    if not isinstance(rails, _ReconcileSnapshot):
        rails = _ReconcileSnapshot(rails)
    today = as_of or rails.now()
    if isinstance(today, datetime):
        today = today.date()
    rules = _rules_by_vendor(mirror.query("SELECT * FROM agreement_rule"))
    actions = _action_rows(mirror)
    name = names_from(rails)
    old = {c["invoice_id"]: c for c in cases(rails, manager)}
    for line in rails.ledger(manager):
        if line.duplicate_of:
            continue
        invoice_actions = [a for a in actions if a["invoice_id"] == line.invoice_id]
        state, findings, desired, fingerprint, eligible = _project(
            rails, line, rules_for(rules, name(line.vendor_id), name(line.manager_id)), invoice_actions,
            today
        )
        # Approval binds the evidence/rules under which an action first entered the outbox.
        # A changed agreement cannot implicitly authorize another copy of an old draft.
        prior_outbox = old.get(line.invoice_id, {}).get("outbox", [])
        obsolete_actions = {
            o["action_id"]
            for o in prior_outbox
            if o.get("action_id") and o.get("fingerprint") != fingerprint
        }
        desired = [o for o in desired if o.get("action_id") not in obsolete_actions]
        acknowledgments = [
            e
            for e in rails.events(manager)
            if e.type == "workflow_resolved"
            and e.invoice_id == line.invoice_id
            and e.payload.get("fingerprint") == fingerprint
        ]
        if eligible and acknowledgments:
            state = "closed"
            desired = []
        previous = {o["id"]: o for o in old.get(line.invoice_id, {}).get("outbox", [])}
        desired_ids = {o["id"] for o in desired}
        for o in previous.values():
            if o["id"] not in desired_ids and o["status"] in ("queued", "needs_approval"):
                record(
                    rails,
                    line.invoice_id,
                    "workflow_outbox",
                    {**o, "status": "cancelled"},
                    today,
                    manager,
                )
        for o in desired:
            prior = previous.get(o["id"])
            if prior:
                o["created_on"] = prior["created_on"]
                if prior["status"] == "delivered":
                    o = prior
            record(rails, line.invoice_id, "workflow_outbox", o, today, manager)
        sources = _case_documents(rails, line, today)
        citations = [{"source_id": d.id, "kind": d.kind.value} for d in sources]
        record(
            rails,
            line.invoice_id,
            "workflow_case",
            {
                "invoice_id": line.invoice_id,
                "vendor": name(line.vendor_id),
                "vendor_id": line.vendor_id,
                "state": state,
                "findings": findings,
                "fingerprint": fingerprint,
                "closure_eligible": eligible,
                "source_ids": [d.id for d in sources],
                "citations": citations,
            },
            today,
            manager,
        )
    return cases(rails, manager)


def resolve(rails, invoice_id, note, by, mirror, manager="keyway"):
    if not note.strip() or not by.strip():
        raise ValueError("Controller identity and explanation are required.")
    current = next(
        (c for c in reconcile(rails, mirror, manager) if c["invoice_id"] == invoice_id), None
    )
    if current is None:
        raise KeyError(invoice_id)
    if not current["closure_eligible"]:
        raise ValueError(
            "Closure requires full payment, matching statement, and verified recipient evidence."
        )
    record(
        rails,
        invoice_id,
        "workflow_resolved",
        {"fingerprint": current["fingerprint"], "note": note, "by": by},
        manager=manager,
    )
    return next(c for c in reconcile(rails, mirror, manager) if c["invoice_id"] == invoice_id)


def acknowledge(rails, mirror, outbox_id, reference, by, manager="keyway"):
    if not reference.strip() or not by.strip():
        raise ValueError("External delivery reference and controller identity are required.")
    row = next(
        (o for c in reconcile(rails, mirror, manager) for o in c["outbox"] if o["id"] == outbox_id),
        None,
    )
    if row is None:
        raise KeyError(outbox_id)
    if row["status"] == "delivered":
        if row.get("reference") != reference:
            raise ValueError("Delivery already binds a different reference.")
        return row
    if row["status"] != "queued":
        raise ValueError("Only a current queued action may be acknowledged as delivered.")
    row = {
        **row,
        "status": "delivered",
        "reference": reference,
        "acknowledged_by": by,
        "delivered_on": rails.now().isoformat(),
    }
    record(rails, row["invoice_id"], "workflow_outbox", row, manager=manager)
    return row
