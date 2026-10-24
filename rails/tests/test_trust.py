import json
from datetime import date
from pathlib import Path

from contracts.models import Document, SourceKind
from rails.matcher import gather_evidence
from rails.trust import decide, trust_rank
from rails.docs import documents_from_events
from world import generate

FIXTURE = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "sample"


def decision(docs, iid):
    return decide(gather_evidence(docs).evidence[iid])


def fixture_docs():
    return [Document(**d) for d in json.loads((FIXTURE / "documents.json").read_text())]


def test_scenario_3_bank_and_vendor_beat_statement():
    d = decision(fixture_docs(), "INV-KW-0301")
    exp = json.loads((FIXTURE / "expected.json").read_text())
    assert (d.claim, d.conflict) == (exp["winning_claim"], exp["conflict"])
    assert {c.source_id for c in d.winners + d.losers} == set(exp["must_cite"])
    assert [c.source_id for c in d.losers] == ["doc-stmt-kw-2026-03"]
    assert "outrank" in d.reason


def test_scenario_9_reply_without_proof_keeps_item_open():
    docs = [x for x in fixture_docs() if x.kind != SourceKind.vendor_notice]
    docs.append(Document(id="doc-reply-1", kind=SourceKind.manager_reply, manager_id="keyway",
                         dated=date(2026, 4, 22), text="Paid last week.",
                         data={"invoice_id": "INV-KW-0301", "status": "paid"}))
    d = decision(docs, "INV-KW-0301")
    assert d.claim == "unpaid" and "doc-reply-1" in {c.source_id for c in d.losers}


def test_claim_alone_with_no_bank_coverage_is_unknown_not_paid():
    docs = [x for x in fixture_docs() if x.kind not in (SourceKind.vendor_notice, SourceKind.manager_bank)]
    d = decision(docs, "INV-KW-0301")
    assert d.claim == "unknown" and not d.conflict


def test_vendor_confirmation_beats_manager_claim_and_bank_beats_statement():
    docs = [x for x in fixture_docs() if x.kind != SourceKind.vendor_notice]
    docs.append(Document(id="vc", kind=SourceKind.vendor_confirmation, manager_id="keyway",
                         vendor_id="v-riogrande", dated=date(2026, 4, 21),
                         data={"invoice_id": "INV-KW-0301", "status": "paid", "amount": 1240.0,
                               "paid_on": "2026-04-21"}))
    assert decision(docs, "INV-KW-0301").claim == "paid"


def test_bank_payment_with_vendor_dispute_is_flagged_conflict():
    docs = fixture_docs()
    for x in docs:
        if x.kind == SourceKind.manager_bank:
            x.data = {"payments": [{"invoice_id": "INV-KW-0301", "amount": 1240.0, "on": "2026-03-10"}]}
    d = decision(docs, "INV-KW-0301")
    assert d.claim == "paid" and d.conflict and "confirm with the vendor" in d.reason


def test_clean_world_all_paid_no_conflicts_and_rank_order():
    docs = documents_from_events(generate().events())
    r = gather_evidence(docs)
    clean = [decide(e) for i, e in r.evidence.items() if i != "INV-KW-0301"]
    assert all(d.claim == "paid" and not d.conflict for d in clean)
    assert trust_rank(SourceKind.manager_bank) > trust_rank(SourceKind.manager_statement)
    assert trust_rank(SourceKind.vendor_confirmation) > trust_rank(SourceKind.manager_reply)


def test_wording_never_accuses():
    d = decision(fixture_docs(), "INV-KW-0301")
    assert not any(w in d.reason.lower() for w in ("fraud", "misuse", "stole", "lied"))
