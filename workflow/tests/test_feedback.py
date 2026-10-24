from datetime import UTC, datetime

import pytest

from agents.skill_registry import load_skill
from contracts.models import LedgerLine
from workflow.cases import CaseStore
from workflow.feedback import FeedbackStore
from workflow.human import Principal
from workflow.memrails import MemoryRails

NOW = datetime(2026, 10, 24, tzinfo=UTC)
CONTROLLER = Principal("controller.dana", "controller")


def setup():
    rails = MemoryRails.from_world((3,), n_clean=0)
    case = CaseStore(rails).ensure(rails.ledger("keyway")[0], NOW)
    decision = CaseStore(rails).record(
        case.id, "supervisor_decision", {"choice": "wait"}, NOW, "decision"
    )
    return rails, case, decision, FeedbackStore(rails)


def test_candidate_evaluation_human_promotion_and_rollback_restart():
    rails, case, decision, store = setup()
    feedback = store.feedback(case.id, decision.id, "Need clearer citations", NOW, "f")
    candidate = store.candidate(
        feedback["id"], "case-investigation", "2", "Cite each source.", ["read"], NOW, "c"
    )
    with pytest.raises(ValueError):
        store.promote(candidate["id"], CONTROLLER, NOW, "premature")
    evaluation = store.run_evaluation(candidate["id"], case_ids=[case.id], now=NOW, key="eval")
    assert evaluation["passed"] is True
    with pytest.raises(PermissionError):
        store.promote(candidate["id"], Principal("agent", "agent"), NOW, "bad")
    store.promote(candidate["id"], CONTROLLER, NOW, "promotion")
    restored = FeedbackStore(MemoryRails(rails.events()))
    assert restored.active_override("case-investigation")["text"] == "Cite each source."
    restored.rollback("case-investigation", CONTROLLER, NOW, "rollback")
    assert restored.active_override("case-investigation") is None


def test_feedback_scope_and_capability_evaluation_boundaries():
    rails, case, decision, store = setup()
    other = CaseStore(rails).ensure(
        LedgerLine(invoice_id="other", manager_id="elsewhere", vendor_id="v", amount=1), NOW
    )
    with pytest.raises(ValueError):
        store.feedback(other.id, decision.id, "wrong scope", NOW, "bad")
    feedback = store.feedback(case.id, decision.id, "note", NOW, "f")
    with pytest.raises(ValueError):
        store.candidate(feedback["id"], "../escape", "2", "text", [], NOW, "c")
    with pytest.raises(ValueError):
        store.candidate(feedback["id"], "case-investigation", "2", "text", ["shell"], NOW, "c")
    with pytest.raises(ValueError):
        store.candidate(
            feedback["id"],
            "case-investigation",
            load_skill("case-investigation")["version"],
            "text",
            [],
            NOW,
            "c",
        )
    candidate = store.candidate(
        feedback["id"], "case-investigation", "2", "text", ["read"], NOW, "c"
    )
    with pytest.raises(ValueError):
        store.evaluate(candidate["id"], [], NOW, "empty")
    with pytest.raises(ValueError):
        store.evaluate(candidate["id"], [{"case_id": other.id, "passed": True}], NOW, "outside")
    store.evaluate(candidate["id"], [{"case_id": case.id, "passed": False}], NOW, "fail")
    with pytest.raises(ValueError):
        store.promote(candidate["id"], CONTROLLER, NOW, "promote")


def test_stale_evaluation_and_duplicate_candidate_versions_fail_closed():
    rails, case, decision, store = setup()
    feedback = store.feedback(case.id, decision.id, "note", NOW, "f")
    candidate = store.candidate(
        feedback["id"], "case-investigation", "2", "text", ["read"], NOW, "c"
    )
    with pytest.raises(ValueError):
        store.candidate(
            feedback["id"], "case-investigation", "2", "different", ["read"], NOW, "other"
        )
    store.run_evaluation(candidate["id"], case_ids=[case.id], now=NOW, key="e")
    CaseStore(rails).update(case.id, 1, NOW, summary="new context")
    with pytest.raises(ValueError, match="stale"):
        store.promote(candidate["id"], CONTROLLER, NOW, "p")


def test_feedback_result_mutation_cannot_rewrite_event_history_and_actions_replay():
    _rails, case, decision, store = setup()
    feedback = store.feedback(case.id, decision.id, "note", NOW, "f")
    candidate = store.candidate(
        feedback["id"], "case-investigation", "2", "text", ["read"], NOW, "c"
    )
    candidate["text"] = "tampered"
    store.run_evaluation(candidate["id"], case_ids=[case.id], now=NOW, key="e")
    promotion = store.promote(candidate["id"], CONTROLLER, NOW, "p")
    assert store.promote(candidate["id"], CONTROLLER, NOW, "p") == promotion
    assert store.active_override("case-investigation")["text"] == "text"
    rollback = store.rollback("case-investigation", CONTROLLER, NOW, "r")
    assert store.rollback("case-investigation", CONTROLLER, NOW, "r") == rollback


def test_attested_passes_cannot_promote_without_measured_replay():
    _rails, case, decision, store = setup()
    feedback = store.feedback(case.id, decision.id, "note", NOW, "f")
    candidate = store.candidate(
        feedback["id"], "case-investigation", "2", "text", ["read"], NOW, "c"
    )
    store.evaluate(candidate["id"], [{"case_id": case.id, "passed": True}], NOW, "attested")
    with pytest.raises(ValueError):
        store.promote(candidate["id"], CONTROLLER, NOW, "p")
    measured = store.run_evaluation(candidate["id"], case_ids=[case.id], now=NOW, key="measured")
    assert measured["passed"]
    assert measured["source"] == "deterministic_native_replay_v1"
    store.promote(candidate["id"], CONTROLLER, NOW, "p")


def test_replay_runner_only_mutates_sandbox_and_must_pass_independent_checks():
    rails, case, decision, store = setup()
    feedback = store.feedback(case.id, decision.id, "note", NOW, "f")
    candidate = store.candidate(
        feedback["id"], "case-investigation", "2", "text", ["read"], NOW, "c"
    )
    before = len(rails.events())

    def malicious_runner(sandbox, replay_case, override, now):
        assert sandbox is not rails
        CaseStore(sandbox).update(replay_case.id, replay_case.version, now, state="resolved")
        return {"passed": True}

    measured = store.run_evaluation(
        candidate["id"], runner=malicious_runner, case_ids=[case.id], now=NOW, key="bad"
    )
    assert measured["passed"] is False
    assert CaseStore(rails).get(case.id).state == "monitoring"
    assert len(rails.events()) == before + 1
    with pytest.raises(ValueError):
        store.promote(candidate["id"], CONTROLLER, NOW, "p")
