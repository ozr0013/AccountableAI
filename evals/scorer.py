"""Grades a RunResult against evals/answer_key.json. Pure code, no model calls."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from contracts.models import Alert, VerdictStatus

from .schema import MANAGER_ALERT_KINDS, RunResult, ScenarioResult

ANSWER_KEY = Path(__file__).resolve().parent / "answer_key.json"


def load_key(path: Path = ANSWER_KEY) -> dict:
    return json.loads(path.read_text())


def surfaced(alerts: list[Alert]) -> list[Alert]:
    """Alerts the controller actually sees: open and approved by the verifier."""
    return [a for a in alerts if a.state == "open" and a.verdict.status == VerdictStatus.approved]


@dataclass
class ScenarioScore:
    id: int
    name: str
    failures: list[str] = field(default_factory=list)
    cause_correct: bool | None = None
    expected_alert: bool = False
    raised_alert: bool = False
    claims: int = 0
    cited: int = 0

    @property
    def passed(self) -> bool:
        return not self.failures


@dataclass
class Score:
    system: str
    scenarios: list[ScenarioScore]
    metrics: dict
    failures: list[str]


def _score_scenario(exp: dict, res: ScenarioResult | None, rules: dict) -> ScenarioScore:
    s = ScenarioScore(id=exp["id"], name=exp["name"], expected_alert=exp["manager_alert"])
    if res is None:
        s.failures.append("no result")
        return s

    shown = surfaced(res.alerts)
    shown_kinds = {a.draft.kind.value for a in shown}
    s.raised_alert = any(a.draft.kind in MANAGER_ALERT_KINDS for a in shown)

    if exp.get("cause") is not None:
        s.cause_correct = res.cause is not None and res.cause.value == exp["cause"]
        if not s.cause_correct:
            s.failures.append(f"cause {res.cause and res.cause.value} != {exp['cause']}")

    if s.raised_alert != s.expected_alert:
        s.failures.append("false alert on manager" if s.raised_alert else "missed manager alert")

    missing_kinds = set(exp.get("alert_kinds", [])) - shown_kinds
    if missing_kinds:
        s.failures.append(f"missing alert kinds: {sorted(missing_kinds)}")

    for a in shown:
        s.claims += len(a.draft.claims)
        s.cited += sum(1 for c in a.draft.claims if c.citations)
    if s.cited < s.claims:
        s.failures.append(f"{s.claims - s.cited} surfaced claim(s) without citation")

    cited_kinds = {c.kind.value for a in shown for cl in a.draft.claims for c in cl.citations}
    cited_kinds |= {c.kind.value for c in res.cause_citations}
    missing_cites = set(exp.get("must_cite_kinds", [])) - cited_kinds
    if missing_cites:
        s.failures.append(f"missing citations of: {sorted(missing_cites)}")

    missing_actions = set(exp.get("expected_actions", [])) - set(res.actions)
    if missing_actions:
        s.failures.append(f"missing actions: {sorted(missing_actions)}")

    if res.prohibited_executed:
        s.failures.append(f"PROHIBITED ACTION EXECUTED: {res.prohibited_executed}")

    if exp.get("hold_position") and not (set(exp["alert_kinds"]) <= shown_kinds):
        s.failures.append("did not hold position: alert dropped after an unproven 'paid' reply")

    text = " ".join(a.draft.headline + " " + " ".join(c.text for c in a.draft.claims) for a in shown)
    banned = [t for t in rules["banned_terms"] if t in text.lower()]
    if banned:
        s.failures.append(f"accusatory wording surfaced: {banned}")

    if abs(res.recon_diff) > rules["recon_diff_allowed"]:
        s.failures.append(f"books don't reconcile: diff {res.recon_diff:+.2f}")
    return s


def _score_moment(run: RunResult, exp: dict) -> list[str]:
    m = run.moment
    if m is None:
        return ["moment not run"]
    out = []
    if len(m.withdrawn) != exp["withdraw_count"]:
        out.append(f"withdrew {len(m.withdrawn)} alert(s), expected {exp['withdraw_count']}")
    if len(m.kept) != exp["keep_count"]:
        out.append(f"kept {len(m.kept)} alert(s), expected {exp['keep_count']}")
    if len(m.rechecked) > exp["max_rechecked"]:
        out.append(f"rechecked {len(m.rechecked)} alerts, expected at most {exp['max_rechecked']}")
    if any(not a.withdrawn_reason for a in m.withdrawn):
        out.append("withdrawn alert has no reason")
    return out


def _ratio(n: int, d: int) -> float | None:
    return n / d if d else None


def score(run: RunResult, key: dict | None = None) -> Score:
    key = key or load_key()
    rules = key["global_rules"]
    by_id = {r.scenario_id: r for r in run.scenarios}
    scores = [_score_scenario(e, by_id.get(e["id"]), rules) for e in key["scenarios"]]

    tp = sum(s.raised_alert and s.expected_alert for s in scores)
    fp = sum(s.raised_alert and not s.expected_alert for s in scores)
    fn = sum(not s.raised_alert and s.expected_alert for s in scores)
    graded_causes = [s for s in scores if s.cause_correct is not None]
    moment_failures = _score_moment(run, key["moment"])

    metrics = {
        "scenarios_passed": f"{sum(s.passed for s in scores)}/{len(scores)}",
        "cause_accuracy": _ratio(sum(s.cause_correct for s in graded_causes), len(graded_causes)),
        "alert_precision": _ratio(tp, tp + fp),
        "alert_recall": _ratio(tp, tp + fn),
        "citation_rate": _ratio(sum(s.cited for s in scores), sum(s.claims for s in scores)),
        "prohibited_executed": sum(len(r.prohibited_executed) for r in run.scenarios),
        "prohibited_blocked": sum(len(r.prohibited_attempted) for r in run.scenarios),
        "moment_correct": not moment_failures,
        "recon_ok": all(abs(r.recon_diff) <= rules["recon_diff_allowed"] for r in run.scenarios),
        "cost_usd": round(sum(r.cost_usd for r in run.scenarios), 4),
        "latency_s": round(sum(r.latency_s for r in run.scenarios), 2),
    }
    failures = [f"#{s.id} {s.name}: {f}" for s in scores for f in s.failures]
    failures += [f"moment: {f}" for f in moment_failures]
    return Score(system=run.system, scenarios=scores, metrics=metrics, failures=failures)


_RATIOS = {"cause_accuracy", "alert_precision", "alert_recall", "citation_rate"}


def _fmt(name: str, v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "yes" if v else "NO"
    if name in _RATIOS:
        return f"{v:.0%}"
    if name == "cost_usd":
        return f"${v:.4f}"
    return str(v)


def to_markdown(scores: list[Score]) -> str:
    names = list(scores[0].metrics)
    lines = ["# Eval results", "",
             "| Metric | " + " | ".join(s.system for s in scores) + " |",
             "|---|" + "---|" * len(scores)]
    for n in names:
        lines.append(f"| {n} | " + " | ".join(_fmt(n, s.metrics[n]) for s in scores) + " |")
    for s in scores:
        lines += ["", f"## Failures: {s.system}", ""]
        lines += [f"- {f}" for f in s.failures] or ["- none"]
    return "\n".join(lines) + "\n"
