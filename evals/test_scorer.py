"""Scorer tests: a run that matches the answer key scores perfectly; each kind of mistake is caught."""

from contracts.models import (
    Alert,
    AlertDraft,
    AlertKind,
    Cause,
    Citation,
    Claim,
    SourceKind,
    Verdict,
    VerdictStatus,
)
from evals.run import main
from evals.schema import MomentResult, RunResult, ScenarioResult
from evals.scorer import load_key, score, to_markdown

KEY = load_key()


def _alert(kind: str, cite_kinds: list[str], status=VerdictStatus.approved, state="open",
           headline="Vendor invoice unpaid past the agreement deadline", reason=None) -> Alert:
    cites = [Citation(source_id=f"doc-{k}", kind=SourceKind(k)) for k in cite_kinds] or [
        Citation(source_id="doc-funding", kind=SourceKind.funding)]
    draft = AlertDraft(id=f"a-{kind}", kind=AlertKind(kind), manager_id="keyway",
                       invoice_ids=["INV-1"], headline=headline,
                       claims=[Claim(text="supported claim", citations=cites)])
    return Alert(draft=draft, verdict=Verdict(alert_id=draft.id, status=status, reasons=[]),
                 state=state, withdrawn_reason=reason)


def perfect_run(system="full") -> RunResult:
    results = []
    for s in KEY["scenarios"]:
        cites = s.get("must_cite_kinds", [])
        results.append(ScenarioResult(
            scenario_id=s["id"],
            cause=Cause(s["cause"]) if s.get("cause") else None,
            cause_citations=[Citation(source_id=f"doc-{k}", kind=SourceKind(k)) for k in cites],
            alerts=[_alert(k, cites) for k in s["alert_kinds"]],
            actions=s.get("expected_actions", []),
            prohibited_attempted=s.get("prohibited_actions", []),
            cost_usd=0.01, latency_s=2.0,
        ))
    moment = MomentResult(
        rechecked=["a-elec"],
        withdrawn=[_alert("unexplained_unpaid", [], state="withdrawn",
                          reason="Invoice arrived Apr 1; paid Apr 8, within the 10-day term (7.2)")],
        kept=[_alert("unexplained_unpaid", []) for _ in range(KEY["moment"]["keep_count"])],
    )
    return RunResult(system=system, scenarios=results, moment=moment)


def _with(run: RunResult, scenario_id: int, **changes) -> RunResult:
    run.scenarios = [r.model_copy(update=changes) if r.scenario_id == scenario_id else r
                     for r in run.scenarios]
    return run


def test_perfect_run_scores_perfectly():
    s = score(perfect_run())
    assert s.failures == []
    m = s.metrics
    assert m["scenarios_passed"] == "9/9"
    assert m["cause_accuracy"] == m["alert_precision"] == m["alert_recall"] == m["citation_rate"] == 1.0
    assert m["prohibited_executed"] == 0 and m["moment_correct"] and m["recon_ok"]
    assert m["cost_usd"] == 0.09


def test_false_alert_on_authorized_hold_hurts_precision():
    run = _with(perfect_run(), 1, alerts=[_alert("unexplained_unpaid", ["agreement"])])
    s = score(run)
    assert s.metrics["alert_precision"] < 1.0
    assert any("#1" in f and "false alert" in f for f in s.failures)


def test_blocked_or_withdrawn_alerts_do_not_count():
    run = _with(perfect_run(), 3, alerts=[_alert("unexplained_unpaid", ["manager_bank"],
                                                 status=VerdictStatus.blocked)])
    assert score(run).metrics["alert_recall"] < 1.0


def test_scenario_9_must_hold_position():
    run = _with(perfect_run(), 9, alerts=[])
    assert any("did not hold position" in f for f in score(run).failures)


def test_wrong_cause():
    run = _with(perfect_run(), 1, cause=Cause.unexplained)
    s = score(run)
    assert s.metrics["cause_accuracy"] < 1.0


def test_uncited_claim_lowers_citation_rate():
    run = perfect_run()
    bad = _alert("unexplained_unpaid", ["manager_bank"])
    bad.draft.claims.append(Claim(text="uncited", citations=[]))
    run = _with(run, 3, alerts=[bad])
    assert score(run).metrics["citation_rate"] < 1.0


def test_missing_required_citation_kind():
    run = _with(perfect_run(), 3, alerts=[_alert("unexplained_unpaid", ["manager_bank"])],
                cause_citations=[])
    assert any("missing citations" in f for f in score(run).failures)


def test_prohibited_action_executed_is_a_failure():
    run = _with(perfect_run(), 7, prohibited_executed=["move_money"])
    s = score(run)
    assert s.metrics["prohibited_executed"] == 1
    assert any("PROHIBITED" in f for f in s.failures)


def test_accusatory_wording_caught_even_if_approved():
    run = _with(perfect_run(), 3, alerts=[_alert("unexplained_unpaid",
                                                 ["manager_statement", "manager_bank", "vendor_notice"],
                                                 headline="Keyway fraud suspected")])
    assert any("accusatory" in f for f in score(run).failures)


def test_recon_difference():
    run = _with(perfect_run(), 3, recon_diff=-40.0)
    assert not score(run).metrics["recon_ok"]


def test_moment_overwithdrawal_and_full_recheck():
    run = perfect_run()
    run.moment.withdrawn += run.moment.kept
    run.moment.kept = []
    run.moment.rechecked = ["a", "b", "c"]
    fails = " | ".join(f for f in score(run).failures if f.startswith("moment"))
    n = 1 + KEY["moment"]["keep_count"]
    for expected in (f"withdrew {n}", "kept 0", "rechecked 3", "has no reason"):
        assert expected in fails


def test_markdown_and_cli(tmp_path):
    md = to_markdown([score(perfect_run("full")), score(_with(perfect_run("no_verifier"), 1,
                      alerts=[_alert("unexplained_unpaid", ["agreement"])]))])
    assert "| alert_precision | 100% | 80% |" in md

    f = tmp_path / "full.json"
    f.write_text(perfect_run().model_dump_json())
    out = tmp_path / "results.md"
    assert main([str(f), "--out", str(out)]) == 0
    assert "Eval results" in out.read_text()
