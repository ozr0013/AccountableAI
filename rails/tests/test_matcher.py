from datetime import date

from contracts.models import Document, SourceKind
from rails.matcher import find_duplicates, gather_evidence
from world import generate
from rails.docs import documents_from_events


def inv(iid, vendor="v1", amount=100.0, received="2026-03-01", text=None, **data):
    return Document(
        id=f"doc-{iid}", kind=SourceKind.vendor_invoice, manager_id="m", vendor_id=vendor,
        dated=date.fromisoformat(received),
        text=text or f"V. Invoice REF-{iid}. Service. Amount due: ${amount}.",
        data={"invoice_id": iid, "amount": amount, "received": received, **data})


def bank(payments, id="bank1"):
    return Document(id=id, kind=SourceKind.manager_bank, manager_id="m",
                    dated=date(2026, 4, 5), data={"payments": payments})


def test_exact_match_by_invoice_id_with_citations():
    r = gather_evidence([inv("a"), bank([{"invoice_id": "a", "amount": 100.0, "on": "2026-03-05"}])])
    e = r.evidence["a"]
    assert e.bank_cents == 10000 and e.cites["bank"][0].source_id == "bank1"
    assert r.unmatched_payments == []


def test_fallback_unique_amount_vendor_window():
    r = gather_evidence([inv("a", amount=123.45),
                         bank([{"vendor_id": "v1", "amount": 123.45, "on": "2026-03-09"}])])
    assert r.evidence["a"].bank_cents == 12345


def test_fallback_refuses_to_guess():
    docs = [inv("a", amount=50.0), inv("b", amount=50.0),   # two equal candidates
            bank([{"vendor_id": "v1", "amount": 50.0, "on": "2026-03-09"},
                  {"vendor_id": "v1", "amount": 50.01, "on": "2026-03-09"},     # near miss
                  {"vendor_id": "v1", "amount": 50.0, "on": "2026-06-30"}])]     # outside window
    r = gather_evidence(docs)
    assert all(e.bank_cents == 0 for e in r.evidence.values())
    assert len(r.unmatched_payments) == 3


def test_fuzzy_hook_resolves_ambiguity():
    docs = [inv("a", amount=50.0), inv("b", amount=50.0),
            bank([{"vendor_id": "v1", "amount": 50.0, "on": "2026-03-09"}])]
    r = gather_evidence(docs, fuzzy=lambda p, cands: sorted(cands)[0])
    assert r.evidence["a"].bank_cents == 5000 and r.evidence["b"].bank_cents == 0


def test_duplicate_same_vendor_invoice_number_in_two_months():
    a = inv("INV-1", received="2026-02-10", text="GreenEdge. Invoice LL-0412. Mowing. Amount due: $300.", amount=300.0)
    b = inv("INV-2", received="2026-03-12", text="GreenEdge. Invoice LL-0412. Mowing. Amount due: $300.", amount=300.0)
    assert find_duplicates([b, a]) == {"INV-2": "INV-1"}


def test_recurring_monthly_with_different_period_is_not_duplicate():
    a = inv("INV-1", amount=300.0, service_period="2026-02", text="G. Invoice A-1. Mowing.")
    b = inv("INV-2", received="2026-03-12", amount=300.0, service_period="2026-03", text="G. Invoice A-2. Mowing.")
    assert find_duplicates([a, b]) == {}


def test_same_period_and_amount_is_duplicate():
    a = inv("INV-1", amount=300.0, service_period="2026-02", text="G. Invoice A-1. Mowing.")
    b = inv("INV-2", received="2026-03-12", amount=300.0, service_period="2026-02", text="G. Invoice A-2. Mowing.")
    assert find_duplicates([a, b]) == {"INV-2": "INV-1"}


def test_clean_world_has_no_duplicates_and_all_payments_matched():
    docs = documents_from_events(generate().events())
    assert find_duplicates(docs) == {}
    assert gather_evidence(docs).unmatched_payments == []
