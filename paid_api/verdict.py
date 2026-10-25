"""The verdict an agent pays for: did the management company pay this vendor invoice?

Built once at startup by replaying the demo world through the real workflow in rules mode
(no model calls, about a second), then answered from the ledger and the open alerts.
Everything here is the same code the AccountableAI dashboard runs; nothing new is decided.
"""

from __future__ import annotations

from functools import lru_cache

UNEXPLAINED = {"unexplained_unpaid", "statement_mismatch"}


@lru_cache(maxsize=1)
def _state(seed: int = 13, n_clean: int = 30):
    from agents import RealAgents
    from contracts.fakes import FakeApprovals, FakeVerifier
    from workflow import Deps, Workflow
    from workflow.book import WorkflowBook
    from workflow.memrails import MemoryRails
    from workflow.run import scan_events
    from world import generate
    from world.generator import ALL_SCENARIOS

    world = generate(seed, ALL_SCENARIOS, n_clean)
    rails = MemoryRails()
    wf = Workflow(Deps(rails, RealAgents(None), FakeVerifier(), FakeApprovals()))
    wf.replay(world.events() + scan_events([m.id for m in world.managers], world.as_of))
    book = WorkflowBook.load(rails)
    vendors = {v.id: v.name for v in world.vendors}
    ledger = {l.invoice_id: l for m in world.managers for l in rails.ledger(m.id)}
    return world, book, vendors, ledger


def invoices() -> list[str]:
    return sorted(_state()[3])


def verdict(invoice_id: str) -> dict | None:
    world, book, vendors, ledger = _state()
    line = ledger.get(invoice_id)
    if line is None:
        return None
    alerts = [a for a in book.open_alerts() if invoice_id in a.draft.invoice_ids]
    # Only these say the money is unaccounted for. A paperwork request, Oakline's own pending
    # approval or a held bank-detail change is a legitimate reason, not a charge on the manager.
    unexplained = [a for a in alerts if a.draft.kind.value in UNEXPLAINED]
    paid = line.paid_amount >= line.amount - 0.005
    if paid:
        status = "paid"
    elif unexplained:
        status = "unexplained"
    elif line.paid_amount > 0:
        status = "partially_paid_explained"   # e.g. a holdback the agreement allows
    else:
        status = "not_paid_explained"   # a legitimate reason: paperwork, not yet due, unfunded
    return {
        "invoice_id": invoice_id,
        "vendor": vendors.get(line.vendor_id, line.vendor_id),
        "middleman": line.manager_id,
        "amount": line.amount,
        "funded": line.funded_amount,
        "paid": line.paid_amount,
        "due_date": line.due_date.isoformat() if line.due_date else None,
        "status": status,
        "alerts": [{
            "kind": a.draft.kind.value,
            "headline": a.draft.headline,
            "recommended_action": a.draft.recommended_action,
            "verifier": a.verdict.status.value,
            "citations": sorted({c.source_id for cl in a.draft.claims for c in cl.citations}),
        } for a in sorted(alerts, key=lambda a: a.draft.kind.value == "recurring_pattern")],
        "as_of": world.as_of.isoformat(),
        "data": "simulated demo world (fictional companies, seed 13)",
    }
