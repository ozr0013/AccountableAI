from datetime import date

import pytest

from chaser_agent.connected import acknowledge, cases, reconcile, resolve
from contracts.models import Document, LedgerLine, SourceKind
from rails.events.ingest import document_event
from workflow.memrails import MemoryRails


class Mirror:
    def __init__(self):
        self.rules = [
            {
                "rule_id": "r",
                "vendor_name": "Vendor",
                "rule_type": "pay_within_days",
                "status": "active",
                "params": {"days": 10},
            }
        ]
        self.actions = []

    def query(self, sql):
        return (
            self.rules if "agreement_rule" in sql else self.actions if "chase_action" in sql else []
        )


def setup():
    rails = MemoryRails()
    rails.now = lambda: date(2026, 3, 12)
    line = LedgerLine(
        invoice_id="INV-KW-0301",
        vendor_id="v-vendor",
        manager_id="keyway",
        amount=100,
        invoice_received=date(2026, 3, 1),
    )
    rails.ledger = lambda manager: [line]
    return rails, Mirror(), line


def doc(rails, kind, ident, data, dated=date(2026, 3, 5), vendor_id=None):
    rails.append_event(
        document_event(
            Document(
                id=ident,
                kind=kind,
                manager_id="keyway",
                dated=dated,
                vendor_id=vendor_id,
                data=data,
            )
        )
    )


def pay(rails, amount=100, vendor="v-vendor", on="2026-03-05"):
    # The stub ledger mirrors the amount that the normal event-derived ledger would observe.
    rails.ledger("keyway")[0].paid_amount += amount
    doc(
        rails,
        SourceKind.manager_bank,
        "bank-" + str(amount) + vendor,
        {
            "payments": [
                {"invoice_id": "INV-KW-0301", "vendor_id": vendor, "amount": amount, "on": on}
            ]
        },
    )


def statement(rails, amount=100, dated=date(2026, 3, 5)):
    doc(
        rails,
        SourceKind.manager_statement,
        "statement",
        {"lines": [{"invoice_id": "INV-KW-0301", "amount": amount, "status": "paid"}]},
        dated=dated,
    )


def test_clean_payment_requests_statement_then_closes_and_replays():
    r, m, _ = setup()
    pay(r)
    c = reconcile(r, m)[0]
    assert c["state"] == "awaiting_statement"
    assert any(x["kind"] == "request_statement" and x["status"] == "queued" for x in c["outbox"])
    before = len(r.events())
    reconcile(r, m)
    assert len(r.events()) == before
    statement(r)
    assert reconcile(r, m)[0]["state"] == "closed"
    assert cases(MemoryRails(r.events()))[0]["state"] == "closed"


def test_missing_agreement_and_pending_controller():
    r, m, _ = setup()
    m.rules = []
    c = reconcile(r, m)[0]
    assert c["state"] == "needs_agreement"
    assert c["outbox"][0]["kind"] == "request_agreement"
    m.rules = Mirror().rules
    m.rules[0]["status"] = "pending_controller"
    c = reconcile(r, m)[0]
    assert c["state"] == "awaiting_controller"
    assert all(x["status"] == "cancelled" for x in c["outbox"])


def test_partial_and_wrong_vendor_never_close():
    r, m, _ = setup()
    pay(r, 40)
    statement(r)
    c = reconcile(r, m)[0]
    assert c["state"] == "needs_controller"
    assert {"partial_payment"} <= {f["code"] for f in c["findings"]}
    assert any(o["kind"] == "request_remainder" for o in c["outbox"])
    with pytest.raises(ValueError):
        resolve(r, "INV-KW-0301", "reviewed", "controller", m)
    r, m, _ = setup()
    pay(r, vendor="v-wrong")
    statement(r)
    c = reconcile(r, m)[0]
    assert c["state"] == "needs_controller"
    assert any(f["code"] == "wrong_vendor" for f in c["findings"])


def test_outbox_delivery_and_stale_cancellation():
    r, m, _ = setup()
    m.rules = []
    o = reconcile(r, m)[0]["outbox"][0]
    assert acknowledge(r, m, o["id"], "external-123", "controller")["status"] == "delivered"
    assert acknowledge(r, m, o["id"], "external-123", "controller")["status"] == "delivered"
    with pytest.raises(ValueError):
        acknowledge(r, m, o["id"], "changed", "controller")
    r, m, _ = setup()
    m.rules = []
    o = reconcile(r, m)[0]["outbox"][0]
    m.rules = Mirror().rules
    reconcile(r, m)
    with pytest.raises(ValueError):
        acknowledge(r, m, o["id"], "late", "controller")


def test_late_payment_human_acknowledgment_is_evidence_bound():
    r, m, _ = setup()
    pay(r, on="2026-03-12")
    statement(r, dated=date(2026, 3, 12))
    c = reconcile(r, m)[0]
    assert c["state"] == "needs_controller"
    assert resolve(r, "INV-KW-0301", "Verified late receipt", "controller", m)["state"] == "closed"
    pay(r, 20)
    assert reconcile(r, m)[0]["state"] == "needs_controller"


def test_account_and_missing_recipient_block_closure():
    r, m, _ = setup()
    m.rules.append(
        {
            "rule_id": "account",
            "vendor_name": "Vendor",
            "rule_type": "pay_to_account",
            "status": "active",
            "params": {"account": "1234", "routing": "123456789"},
        }
    )
    pay(r)
    statement(r)
    c = reconcile(r, m)[0]
    assert c["state"] == "needs_controller"
    assert any(f["code"] == "missing_payment_identity" for f in c["findings"])


def test_chaser_approval_projects_outbox_without_duplicate_actions():
    r, m, _ = setup()
    m.actions = [
        {
            "action_id": "a",
            "invoice_id": "INV-KW-0301",
            "action": "reminder",
            "status": "needs_approval",
            "message": "Please pay",
            "as_of": "2026-03-12",
        }
    ]
    c = reconcile(r, m)[0]
    assert c["outbox"][0]["status"] == "needs_approval"
    with pytest.raises(ValueError):
        acknowledge(r, m, c["outbox"][0]["id"], "ref", "controller")
    m.actions[0]["status"] = "approved"
    c = reconcile(r, m)[0]
    assert sum(o["status"] == "queued" for o in c["outbox"]) == 1
    before = len(r.events())
    reconcile(r, m)
    assert len(r.events()) == before


def test_statement_predating_payment_does_not_close():
    r, m, _ = setup()
    pay(r, on="2026-03-10")
    statement(r)
    c = reconcile(r, m)[0]
    assert c["state"] != "closed"
    assert any(f["code"] == "statement_predates_payment" for f in c["findings"])


def test_obsolete_chaser_action_is_not_requeued_on_rule_change():
    r, m, _ = setup()
    m.actions = [
        {
            "action_id": "a",
            "invoice_id": "INV-KW-0301",
            "action": "reminder",
            "status": "approved",
            "message": "Please pay",
            "as_of": "2026-03-12",
        }
    ]
    assert reconcile(r, m)[0]["outbox"][0]["status"] == "queued"
    m.rules[0]["params"]["days"] = 5
    c = reconcile(r, m)[0]
    assert not any(o["status"] == "queued" for o in c["outbox"])


def test_bank_before_invoice_and_contract_amount_difference():
    r, m, _ = setup()
    pay(r, on="2026-02-28")
    statement(r)
    c = reconcile(r, m)[0]
    assert any(f["code"] == "invalid_payment_date" for f in c["findings"])
    assert not c["closure_eligible"]
    r, m, _ = setup()
    m.rules.append(
        {
            "rule_id": "amount",
            "vendor_name": "Vendor",
            "rule_type": "amount",
            "status": "active",
            "params": {"amount": 90, "per": "fixed"},
        }
    )
    pay(r)
    statement(r)
    c = reconcile(r, m)[0]
    assert any(f["code"] == "agreement_amount_mismatch" for f in c["findings"])
    assert c["state"] == "needs_controller"


def test_contract_invoice_date_basis_requires_invoice_date_evidence():
    r, m, _ = setup()
    m.rules[0]["params"]["from"] = "invoice date"
    pay(r)
    statement(r)
    c = reconcile(r, m)[0]
    assert c["state"] == "needs_controller"
    assert not c["closure_eligible"]


def test_statement_row_vendor_and_unpaid_status_block_closure():
    for fields in (
        {"vendor_id": "v-other", "status": "paid"},
        {"vendor_id": "v-vendor", "status": "unpaid"},
    ):
        r, m, _ = setup()
        pay(r)
        doc(
            r,
            SourceKind.manager_statement,
            "bad-statement",
            {"lines": [{"invoice_id": "INV-KW-0301", "amount": 100, **fields}]},
        )
        c = reconcile(r, m)[0]
        assert c["state"] == "needs_controller"
        assert not c["closure_eligible"]
        with pytest.raises(ValueError):
            resolve(r, "INV-KW-0301", "reviewed", "controller", m)


def test_case_exposes_source_documents_and_delivery_date():
    r, m, _ = setup()
    pay(r)
    c = reconcile(r, m)[0]
    assert "bank-100v-vendor" in c["source_ids"]
    assert any(citation["source_id"] == "bank-100v-vendor" for citation in c["citations"])
    assert any(
        "bank-100v-vendor" in event["payload"].get("source_ids", []) for event in c["history"]
    )
    o = next(o for o in c["outbox"] if o["kind"] == "request_statement")
    delivered = acknowledge(r, m, o["id"], "external-ref", "controller")
    assert delivered["delivered_on"] == "2026-03-12"
    assert acknowledge(r, m, o["id"], "external-ref", "controller") == delivered


def test_vendor_confirmation_routes_to_bank_proof_review():
    r, m, line = setup()
    line.paid_amount = 100
    doc(
        r,
        SourceKind.vendor_confirmation,
        "vendor-paid",
        {"invoice_id": line.invoice_id, "status": "paid", "amount": 100, "paid_on": "2026-03-05"},
    )
    statement(r)
    c = reconcile(r, m)[0]
    assert c["state"] == "needs_controller"
    assert any(
        o["kind"] == "request_payment_proof" and o["status"] == "queued" for o in c["outbox"]
    )
    assert "vendor-paid" in c["source_ids"]
    assert not c["closure_eligible"]


def test_unique_fallback_bank_match_is_reviewed_and_cited():
    r, m, line = setup()
    line.paid_amount = 100
    doc(
        r,
        SourceKind.vendor_invoice,
        "invoice",
        {"invoice_id": line.invoice_id, "amount": 100, "received": "2026-03-01"},
        vendor_id="v-vendor",
    )
    doc(
        r,
        SourceKind.manager_bank,
        "fallback-bank",
        {"payments": [{"vendor_id": "v-vendor", "amount": 100, "on": "2026-03-05"}]},
    )
    statement(r)
    c = reconcile(r, m)[0]
    assert c["state"] == "closed"
    assert "fallback-bank" in c["source_ids"]


def test_conflicting_active_deadlines_require_controller():
    r, m, _ = setup()
    m.rules.append({**m.rules[0], "rule_id": "r-conflicting", "params": {"days": 20}})
    pay(r)
    statement(r)
    c = reconcile(r, m)[0]
    assert c["state"] == "awaiting_controller"
    assert any(f["code"] == "conflicting_deadlines" for f in c["findings"])


def test_reconciliation_reads_event_log_once_for_a_hundred_cases():
    class CountingRails(MemoryRails):
        event_reads = 0
        document_reads = 0
        ledger_reads = 0

        def events(self, manager_id=None):
            self.event_reads += 1
            return super().events(manager_id)

        def documents(self, manager_id=None, invoice_id=None):
            self.document_reads += 1
            return super().documents(manager_id, invoice_id)

        def ledger(self, manager):
            self.ledger_reads += 1
            return [
                LedgerLine(
                    invoice_id=f"INV-KW-{i:04}",
                    vendor_id="v-vendor",
                    manager_id=manager,
                    amount=100,
                    invoice_received=date(2026, 3, 1),
                )
                for i in range(100)
            ]

    rails = CountingRails()
    rails.now = lambda: date(2026, 3, 12)
    m = Mirror()
    m.rules = []
    result = reconcile(rails, m)
    assert len(result) == 100
    assert all(c["state"] == "needs_agreement" for c in result)
    assert sum(len(c["outbox"]) for c in result) == 100
    assert rails.event_reads <= 4
    assert rails.document_reads == 1
    assert rails.ledger_reads == 1
    count = len(rails._events)
    assert len(reconcile(rails, m)) == 100
    assert len(rails._events) == count


def test_anonymous_payment_does_not_reassign_after_partial_explicit_payment():
    from rails.matcher import gather_evidence

    r, m, line = setup()
    line.paid_amount = 20
    doc(
        r,
        SourceKind.vendor_invoice,
        "invoice",
        {"invoice_id": line.invoice_id, "amount": 100, "received": "2026-03-01"},
        vendor_id="v-vendor",
    )
    doc(
        r,
        SourceKind.manager_bank,
        "mixed-bank",
        {
            "payments": [
                {
                    "invoice_id": line.invoice_id,
                    "vendor_id": "v-vendor",
                    "amount": 20,
                    "on": "2026-03-05",
                },
                {"vendor_id": "v-vendor", "amount": 100, "on": "2026-03-05"},
            ]
        },
    )
    statement(r)
    evidence = gather_evidence(r.documents()).evidence[line.invoice_id]
    assert evidence.bank_cents == 2000
    c = reconcile(r, m)[0]
    assert not c["closure_eligible"]
    assert any(f["code"] == "partial_payment" and "20" in f["message"] for f in c["findings"])
    assert not any(f["code"] == "overpayment" for f in c["findings"])
