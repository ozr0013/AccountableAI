"""Evidence-backed post-payment review. Outputs are records and approval-gated drafts.

A management statement is supporting evidence, never proof of a bank payment. Each revision
supersedes the previous review logically through `previous_review_id`; no history is edited.
"""
from __future__ import annotations

import json
from datetime import date

from contracts.models import Event, SourceKind
from rails.facts import terms_at
from rails.guards.payments import inspection_condition, payable_cents
from rails.matcher import gather_evidence

from .book import digest, wf_event


def review_payments(rails, trigger: Event, manager_id: str, invoice_ids: list[str],
                    today: date) -> list[str]:
    docs = [d for d in rails.documents(manager_id) if d.dated is None or d.dated <= today]
    history = rails.events(manager_id)
    matched = gather_evidence(docs).matched_payments
    by_id = {d.id: d for d in docs}

    notes = []
    for line in rails.ledger(manager_id):
        if line.invoice_id not in invoice_ids or line.duplicate_of:
            continue
        payments = [(d, p) for d in docs if d.kind == SourceKind.manager_bank
                    for p in d.data.get("payments", [])
                    if p.get("invoice_id") == line.invoice_id
                    and (not p.get("on") or date.fromisoformat(p["on"]) <= today)]
        payments += [(by_id[doc_id], p) for iid, doc_id, p in matched
                     if iid == line.invoice_id and not p.get("invoice_id")
                     and (not p.get("on") or date.fromisoformat(p["on"]) <= today)]
        prior = [e for e in history if e.type == "payment_reviewed"
                 and e.invoice_id == line.invoice_id]
        existing = next((e for e in prior if e.payload.get("trigger") == trigger.id), None)
        if existing:
            _queue_outputs(rails, existing, history)
            continue
        if not payments and not prior:
            continue
        findings = []
        evidence = {d.id for d, _ in payments}
        evidence.update(d.id for d in docs if d.kind == SourceKind.vendor_invoice
                        and d.data.get("invoice_id") == line.invoice_id)

        def finding(code, detail, findings=findings, evidence=evidence):
            findings.append({"code": code, "detail": detail,
                             "source_ids": sorted(evidence)})

        valid = [p for d, p in payments if p.get("vendor_id") == line.vendor_id and d.manager_id == manager_id]
        if not payments:
            finding("payment_evidence_removed", "Previously reviewed bank payment is no longer current.")
        if any(not p.get("vendor_id") for _, p in payments):
            finding("recipient_unknown", "Bank record omits the recipient; vendor identity needs confirmation.")
        if len(valid) != len(payments):
            finding("wrong_vendor", f"Bank recipient differs from invoice vendor {line.vendor_id}.")
        actual = sum(round(p["amount"] * 100) for p in valid)
        expected = round(line.amount * 100)
        terms = terms_at(history, manager_id, line.invoice_received, line.vendor_id) or {}
        invoice_data = next((d.data for d in docs if d.kind == SourceKind.vendor_invoice
                             and d.data.get("invoice_id") == line.invoice_id), {})
        required = payable_cents(line.amount, terms, invoice_data)
        allowed_holdback = expected - required
        condition = terms.get("holdback_condition")
        if terms.get("holdback_pct") and condition and not inspection_condition(condition):
            finding("holdback_condition_unverified", "Contract release condition requires human review: " + str(condition))
        if allowed_holdback:
            evidence.update(d.id for d in docs if d.kind == SourceKind.agreement)
        if actual < required or actual > expected:
            finding("underpayment" if actual < expected else "overpayment",
                    f"Invoice {expected / 100:.2f}; payable now {required / 100:.2f}; "
                    f"paid to expected vendor {actual / 100:.2f}.")
        dates = [date.fromisoformat(p["on"]) for p in valid if p.get("on")]
        if line.due_date and dates and max(dates) > line.due_date:
            evidence.update(d.id for d in docs if d.kind == SourceKind.agreement)
            finding("late_payment", f"Payment dated {max(dates)}; agreement deadline {line.due_date}.")
        if not line.due_date:
            finding("deadline_unknown", "No confirmed agreement deadline is available.")
        if any(not p.get("on") for _, p in payments):
            finding("payment_date_missing", "Bank record has no payment date; timing is unverified.")

        last_payment = max((date.fromisoformat(p["on"]) for _, p in payments if p.get("on")),
                           default=None)
        statements = [(d, entry) for d in docs if d.kind == SourceKind.manager_statement
                      and d.dated and last_payment and d.dated >= last_payment
                      for entry in d.data.get("lines", [])
                      if entry.get("invoice_id") == line.invoice_id]
        if not statements:
            finding("statement_missing", "Request a management statement covering this payment.")
        else:
            statement, entry = max(statements, key=lambda pair: (pair[0].dated, pair[0].id))
            evidence.add(statement.id)
            if round(entry.get("amount", 0) * 100) != actual or entry.get("status") != "paid":
                finding("statement_amount_mismatch", "Management statement disagrees with bank payment.")

        payload = {"invoice_id": line.invoice_id, "vendor_id": line.vendor_id,
                   "expected_amount": expected / 100, "paid_amount": actual / 100,
                   "required_amount": required / 100, "allowed_holdback": allowed_holdback / 100,
                   "due_date": line.due_date.isoformat() if line.due_date else None,
                   "findings": findings, "evidence": sorted(evidence),
                   "status": "needs_review" if findings else "verified"}
        fingerprint = digest(json.dumps(payload, sort_keys=True))
        if prior and prior[-1].payload.get("fingerprint") == fingerprint:
            _queue_outputs(rails, prior[-1], history)
            continue
        payload.update(fingerprint=fingerprint, previous_review_id=prior[-1].id if prior else None)
        review = wf_event("payment_reviewed", line.invoice_id, trigger, payload,
                          manager_id, line.invoice_id)
        rails.append_event(review)
        _queue_outputs(rails, review, history)
        notes.append(f"{line.invoice_id}: payment review {payload['status']} "
                     f"({', '.join(f['code'] for f in findings) or 'checks passed'})")
    return notes


def _queue_outputs(rails, review: Event, history: list[Event]) -> None:
    """Repair partial writes on retry using the original review's stable id and timestamps."""
    p = review.payload
    rails.append_event(wf_event("finance_update_queued", review.id, review,
        {"review_id": review.id, "recipients": ["management", "owner"],
         "status": p["status"], "findings": p["findings"], "evidence": p["evidence"],
         "delivery": "internal_queue"}, review.manager_id, review.invoice_id))
    requested = any(e.type == "statement_request_drafted" and e.invoice_id == review.invoice_id
                    for e in history)
    if any(f["code"] == "statement_missing" for f in p["findings"]) and not requested:
        rails.append_event(wf_event("statement_request_drafted", review.invoice_id, review,
            {"invoice_id": review.invoice_id, "review_id": review.id,
             "requires_approval": True, "recipient": "management",
             "body": f"Please provide a management statement and payment details for "
                     f"invoice {review.invoice_id} ({p['vendor_id']}).",
             "evidence": p["evidence"]}, review.manager_id, review.invoice_id))
