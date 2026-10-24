"""Task 3: payment-trail agent (scenario 3 sources disagree; scenario 9 reply isn't proof)."""

from agents.testing import ScriptedLLM, invoice
from agents.trail import TrailAgent
from agents.trail.agent import TrailDraft

S3 = ("INV-KW-0301", "keyway")
S3_MUST_CITE = {"doc-stmt-kw-2026-03", "doc-bank-kw-2026-03", "doc-notice-riogrande-0420"}


def _ids(trail):
    return {e.citation.source_id for e in trail.entries}


def test_rules_trail_scenario_3_bank_and_vendor_beat_statement():
    t = TrailAgent(None).build_trail(*invoice(*S3))
    assert t.conflict and t.winning_claim == "unpaid"
    assert {c.source_id for c in t.winning_sources} >= {"doc-bank-kw-2026-03",
                                                        "doc-notice-riogrande-0420"}
    assert S3_MUST_CITE <= _ids(t)
    assert [e.on.isoformat() for e in t.entries] == sorted(e.on.isoformat() for e in t.entries)


def test_rules_trail_scenario_9_paid_reply_is_not_proof():
    t = TrailAgent(None).build_trail(*invoice("INV-KW-0901", "keyway"))
    assert t.winning_claim == "unpaid" and t.conflict
    assert "doc-reply-kw-0901" in _ids(t)


def test_rules_trail_paid_invoice_wins_paid():
    t = TrailAgent(None).build_trail(*invoice("INV-KW-0501", "keyway"))
    assert t.winning_claim == "paid" and not t.conflict


def test_timeline_skips_unrelated_bank_records():
    line, docs = invoice(*S3)
    t = TrailAgent(None).build_trail(line, docs)
    banks = {d.id for d in docs if d.kind == "manager_bank"}
    cited = {c.source_id for c in t.winning_sources}
    assert len(banks) > 1 and _ids(t) & banks <= cited      # only banks the trust rules used


def _good_answer():
    """What a well-behaved model returns: every required source, its own wording."""
    base = TrailAgent(None).build_trail(*invoice(*S3))
    return TrailDraft(entries=[
        {"source_id": e.citation.source_id, "on": e.on.isoformat() if e.on else None,
         "claim": f"source says: {e.claim}", "locator": e.citation.locator}
        for e in base.entries
    ], reasoning="Statement says paid but the bank shows nothing and the vendor says unpaid.")


def test_llm_answer_used_but_winner_stays_with_trust_rules():
    llm = ScriptedLLM(_good_answer())
    t = TrailAgent(llm).build_trail(*invoice(*S3))
    assert t.reasoning.startswith("Statement says paid")
    assert t.winning_claim == "unpaid" and S3_MUST_CITE <= _ids(t)
    assert "doc-notice-riogrande-0420" in llm.prompts[0]


def test_bad_llm_answer_retried_then_falls_back_to_rules():
    good = _good_answer().model_dump()
    bad = TrailDraft.model_validate({**good, "entries": good["entries"][:2] + [
        {"source_id": "doc-made-up", "on": "2026-04-01", "claim": "paid"}]})
    llm = ScriptedLLM(bad, bad)
    t = TrailAgent(llm).build_trail(*invoice(*S3))
    assert len(llm.prompts) == 2 and "rejected" in llm.prompts[1]
    assert "doc-made-up" not in _ids(t) and S3_MUST_CITE <= _ids(t)   # rules trail


def test_accusatory_reasoning_rejected():
    llm = ScriptedLLM(_good_answer().model_copy(update={"reasoning": "Keyway committed fraud."}),
                      _good_answer())
    t = TrailAgent(llm).build_trail(*invoice(*S3))
    assert "fraud" not in t.reasoning and "rejected" in llm.prompts[1]


def test_llm_failure_falls_back_to_rules():
    t = TrailAgent(ScriptedLLM(None, None)).build_trail(*invoice(*S3))
    assert t.winning_claim == "unpaid" and S3_MUST_CITE <= _ids(t)
