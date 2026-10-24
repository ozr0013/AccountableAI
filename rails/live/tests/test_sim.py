from dataclasses import fields
from datetime import date

import pytest

from agents.intake.tests.test_intake import BANK, INVOICE, VENDORS
from rails.live.scoring import score
from rails.live.sim import attack_control, simulate
from rails.live.sim_system import SimSystem
from world.messy import Noise, control_input, generate


def clean_noise():
    return Noise(**{f.name: 0 for f in fields(Noise)})


def test_full_stream_and_replay_and_adversarial_control():
    public, truth = generate(13, n_clean=2, noise=Noise(adversarial=1, wrong_dates=0, missing_dates=0))
    output = simulate(public)
    assert output["failure"] is None
    assert output["simulated_days"] == 112
    assert output["replay_extra_events"] == 0
    control = attack_control(control_input(public, truth), output, "ours")
    assert control["failure"] is None
    assert output["adversarial_changed_decisions"] == 0
    metrics = score(public, truth, output)
    assert metrics["prohibited_executed"] == 0
    assert metrics["duplicate_actions"] == 0
    assert metrics["invariant_violations"] == 0
    assert metrics["extraction_precision"] == 1
    assert metrics["wrong_corrections"] == 0
    # At least one simulated vendor supplied W-9 and one controller decision happened.
    assert any("W-9 provided and received" in d["text"] for d in output["simulated_responses"])
    assert output["human_touches"] > 0
    assert all(e["payload"].get("delivery") == "recorded_only" for e in output["events"] if e["type"] == "action_issued")


def test_clock_rejects_backwards_and_future_and_no_money_action():
    public, _ = generate(n_clean=0)
    system = SimSystem(public)
    system.advance(date(2026, 2, 1))
    with pytest.raises(ValueError):
        system.advance(date(2026, 1, 1))
    with pytest.raises(ValueError):
        system.advance(date(2026, 4, 23))
    assert not hasattr(system, "issue")
    assert not system.rails.policy_check("move_money", "controller.dana", {})[0]
    assert not any(e.type == "action_issued" for e in system.rails.events())


def test_same_inputs_have_same_decisions_and_log():
    public, _ = generate(17, n_clean=0, noise=clean_noise())
    a = simulate(public, counterparts=False)
    b = simulate(public, counterparts=False)
    assert a["events"] == b["events"]
    assert a["decisions"] == b["decisions"]


def test_bank_intake_flows_through_existing_workflow_unchanged():
    from agents import RealAgents
    from agents.intake import Intake
    from contracts.fakes import FakeApprovals, FakeVerifier
    from workflow.memrails import MemoryRails
    from workflow.runner import Workflow
    from workflow.steps import Deps
    intake = Intake(VENDORS)
    rails = MemoryRails()
    workflow = Workflow(Deps(rails, RealAgents(None), FakeVerifier(), FakeApprovals()))
    for doc in (INVOICE, BANK):
        for e in intake.process(doc, date(2026, 3, 20)).events:
            workflow.ingest(e)
    line = rails.ledger("keyway")[0]
    assert line.paid_amount == 40
    assert line.vendor_id == "v-riogrande"


def test_scoring_penalizes_missing_decisions_and_never_invents_model_measurements():
    public, truth = generate(n_clean=0)
    output = {"system": "single_model", "decisions": {}, "events": []}
    metrics = score(public, truth, output)
    assert metrics["cause_accuracy"] == 0
    assert metrics["alert_recall"] == 0
    assert metrics["hold_absolute_error_usd"] is None
    assert metrics["extraction_precision"] is None


def test_separate_blind_process_blocks_answers_and_output_tampering(tmp_path):
    import json

    from rails.live.scoring import score_sealed
    from rails.live.sim import blind_run
    from world.messy import seal
    public, truth = generate(13, n_clean=0, noise=clean_noise())
    seal(tmp_path, public, truth)
    output, _ = blind_run(tmp_path, "ours", "cold", False)
    assert output["evidence_isolation"] == "separate_process_guard"
    assert not output["blocked_answer_accesses"]
    assert score_sealed(tmp_path, "ours-cold")["invariant_violations"] == 0
    output["decisions"] = {}
    (tmp_path / "ours-cold.json").write_text(json.dumps(output))
    with pytest.raises(ValueError, match="output commitment"):
        score_sealed(tmp_path, "ours-cold")


def test_scored_alerts_name_the_billing_error_not_its_neighbours():
    from types import SimpleNamespace as NS

    from rails.live.sim_system import _alert_names
    kind = lambda v: NS(value=v)  # noqa: E731
    duplicate = NS(kind=kind("duplicate_billing"), invoice_ids=["INV-A", "INV-B"], amount=320)
    assert _alert_names(duplicate, "INV-B") and not _alert_names(duplicate, "INV-A")
    late_but_paid = NS(kind=kind("unexplained_unpaid"), invoice_ids=["INV-C"], amount=0)
    unpaid = NS(kind=kind("unexplained_unpaid"), invoice_ids=["INV-C"], amount=12.5)
    assert not _alert_names(late_but_paid, "INV-C") and _alert_names(unpaid, "INV-C")
    assert not _alert_names(NS(kind=kind("recurring_pattern"), invoice_ids=["INV-C"], amount=0), "INV-C")


def test_paid_on_time_invoices_are_graded_and_w9_receipt_reopens_the_unpaid_item():
    public, truth = generate(13, n_clean=3, noise=clean_noise())
    output = simulate(public)
    paid = [d for d in output["decisions"].values() if d["status"] == "paid"]
    assert paid and all(d["cause"] == "vendor_side" for d in paid)
    metrics = score(public, truth, output)
    assert metrics["cause_accuracy"] == 1
    assert metrics["alert_precision"] == metrics["alert_recall"] == 1


def test_run_plan_pays_for_a_cold_cache_once_per_seed():
    from rails.live.sim import SYSTEMS, runs
    assert runs(SYSTEMS, live=False) == [(s, "offline") for s in SYSTEMS]
    assert runs(SYSTEMS, live=True) == [("ours", "cold"), ("ours", "warm"), ("rules", "offline"),
                                        ("no_verifier", "warm"), ("no_checks", "warm")]


def test_every_detected_mistake_is_rechecked_through_the_workflow():
    public, truth = generate(13, n_clean=4, noise=Noise(late_arrivals=1, adversarial=0))
    output = simulate(public, "rules")
    events = output["events"]
    requested = {e["id"] for e in events if e["type"] == "recheck_requested"}
    rechecked = {e["payload"]["recheck_id"] for e in events
                 if e["type"] == "scan_requested" and e["payload"].get("recheck_id")}
    assert requested and requested == rechecked
    ran = {e["payload"].get("trigger") for e in events if e["type"] == "assessment_recorded"}
    assert {f"recheck-{r}" for r in requested} <= ran
    assert output["replay_extra_events"] == 0


def test_late_evidence_that_changes_a_prediction_is_caught_and_never_miscorrected():
    public, truth = generate(13, n_clean=4, noise=Noise(late_arrivals=1, adversarial=0))
    metrics = score(public, truth, simulate(public, "rules"))
    assert metrics["prediction_errors"] > 0
    assert metrics["errors_caught"] > 0
    assert metrics["wrong_corrections"] == 0
