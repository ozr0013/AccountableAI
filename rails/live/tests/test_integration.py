from datetime import date

from contracts.models import Document, SourceKind
from rails.live.system import LiveSystem

VENDORS = [{"id": "v-riogrande", "name": "Rio Grande Plumbing", "trade": "plumber"}]
AGREEMENT = """7.2 Vendor Payment. Manager shall pay each vendor invoice within ten (10) days of receipt of a complete invoice, using funds provided by Owner.
7.4 Holdback. Manager may withhold up to ten percent (10%) of a repair invoice until the work passes inspection.
8.1 Approval Limit. Any single repair exceeding $5,000 requires Owner's written approval and funding before work is ordered."""


def make_system(rails=None):
    return LiveSystem({"vendors": VENDORS, "start": "2026-01-01", "as_of": "2026-04-22",
                       "simulated": False, "manager_id": "keyway"}, rails=rails)


def document(did, kind, text, dated="2026-03-01"):
    return Document(id=did, kind=SourceKind(kind), manager_id="keyway", dated=date.fromisoformat(dated), text=text)


def establish_unpaid(system):
    system.advance(date(2026, 4, 22))
    system.ingest(document("agreement", "agreement", AGREEMENT, "2026-01-01"))
    pending = system.pending_terms()
    assert len(pending) == 1
    assert not any(e.type == "terms_confirmed" for e in system.rails.events())
    system.confirm_terms(next(iter(pending)), "controller.dana")
    assert not system.pending_terms()
    system.ingest(document("invoice", "vendor_invoice",
        "Rio Grande Plbg LLC\nInvoice INV-KW-0301\nReceived: 2026-03-01\nAmount due: $100.00"))
    system.ingest(document("funding", "funding",
        "Oakline Homes -> Keyway Property Management\nAllocation INV-KW-0301: $100.00\nValue date 2026-03-02", "2026-03-02"))


def test_existing_workflow_owns_unpaid_reply_bank_and_case_update():
    system = make_system()
    establish_unpaid(system)
    output = system.output()
    assert output["orchestrator"] == "workflow.runner.Workflow"
    assert output["decisions"]["INV-KW-0301"]["alert"] == "unexplained_unpaid"
    assert any(e.type == "chase_sent" for e in system.rails.events())
    system.review_funding(18400)
    assert system.output()["cases"][-1]["case"]["proposed_hold"] == 100
    system.ingest(document("reply", "manager_reply", "From: Keyway\nRe: INV-KW-0301\npaid", "2026-04-20"))
    assert system.output()["decisions"]["INV-KW-0301"]["alert"] == "unexplained_unpaid"
    bank = document("proof", "manager_bank", "Keyway operating account export\nDate,Debit,Memo,Transaction\n2026-03-05,$100.00,Rio Grande Plumbing INV-KW-0301,TX-1", "2026-03-05")
    system.ingest(bank)
    assert system.rails.ledger("keyway")[0].paid_amount == 100
    assert system.output()["decisions"]["INV-KW-0301"]["alert"] is None
    assert any(e.type == "alert_withdrawn" for e in system.rails.events())
    assert any(e.type == "mistake_detected" and e.payload["signal"] == "outcome" for e in system.rails.events())
    system.review_funding(18400)
    assert system.output()["cases"][-1]["case"]["proposed_hold"] == 0
    count = len(system.rails.events())
    system.ingest(bank)
    assert len(system.rails.events()) == count


def test_restart_restores_intake_and_existing_workflow_interrupt():
    from workflow.memrails import MemoryRails
    first = make_system()
    first.advance(date(2026, 4, 22))
    doc = document("agreement", "agreement", AGREEMENT, "2026-01-01")
    first.ingest(doc)
    restored = make_system(MemoryRails(first.rails.events()))
    assert restored.pending_terms()
    restored.confirm_terms(next(iter(restored.pending_terms())))
    count = len(restored.rails.events())
    restored.ingest(doc)
    assert len(restored.rails.events()) == count


def test_adapter_exposes_no_independent_action_or_alert_engine():
    system = make_system()
    assert not hasattr(system, "issue")
    assert not hasattr(system, "_transition")
    assert not system.rails.policy_check("move_money", "controller.dana", {})[0]
