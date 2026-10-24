"""Task 3: payment trail (scenario 3 sources disagree; scenario 9 reply isn't proof)."""

from agents.testing import invoice
from agents.trail import build_trail

S3 = ("INV-KW-0301", "keyway")
S3_MUST_CITE = {"doc-stmt-kw-2026-03", "doc-bank-kw-2026-03", "doc-notice-riogrande-0420"}


def _ids(trail):
    return {e.citation.source_id for e in trail.entries}


def test_rules_trail_scenario_3_bank_and_vendor_beat_statement():
    t = build_trail(*invoice(*S3))
    assert t.conflict and t.winning_claim == "unpaid"
    assert {c.source_id for c in t.winning_sources} >= {"doc-bank-kw-2026-03",
                                                        "doc-notice-riogrande-0420"}
    assert S3_MUST_CITE <= _ids(t)
    assert [e.on.isoformat() for e in t.entries] == sorted(e.on.isoformat() for e in t.entries)


def test_rules_trail_scenario_9_paid_reply_is_not_proof():
    t = build_trail(*invoice("INV-KW-0901", "keyway"))
    assert t.winning_claim == "unpaid" and t.conflict
    assert "doc-reply-kw-0901" in _ids(t)


def test_rules_trail_paid_invoice_wins_paid():
    t = build_trail(*invoice("INV-KW-0501", "keyway"))
    assert t.winning_claim == "paid" and not t.conflict


def test_timeline_skips_unrelated_bank_records():
    line, docs = invoice(*S3)
    t = build_trail(line, docs)
    banks = {d.id for d in docs if d.kind == "manager_bank"}
    cited = {c.source_id for c in t.winning_sources}
    assert len(banks) > 1 and _ids(t) & banks <= cited      # only banks the trust rules used
