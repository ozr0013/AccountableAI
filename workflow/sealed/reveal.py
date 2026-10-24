"""Step 3, reveal: re-hash everything against the commitment, then (only then) grade.

Order matters: the answers are read only after their hashes match the ones made before the run,
and the outputs only after their hash matches the one written right after the run. Grading uses
Omar's scorer and answer key unchanged (evals/ is read-only here).
"""

from __future__ import annotations

import json
from pathlib import Path

from contracts.models import Alert, AlertKind, Cause, Citation

from .common import COMMITMENT, INPUTS, OUTPUTS, OUTPUTS_HASH, REPORT, TRUTH, sha256_file


def verify_hashes(out: Path) -> tuple[bool, list[dict]]:
    """Re-hash every committed file. Returns (all match, one row per check)."""
    c = json.loads((out / COMMITMENT).read_text())
    rows = [{"what": f"answer file {Path(p).name}", "path": p, "committed": h,
             "now": sha256_file(Path(p)) if Path(p).exists() else "MISSING"}
            for p, h in c["sealed_files"].items()]
    rows.append({"what": "inputs (events.jsonl)", "path": str(out / INPUTS),
                 "committed": c["inputs_sha256"], "now": sha256_file(out / INPUTS)})
    rows.append({"what": "outputs (outputs.json)", "path": str(out / OUTPUTS),
                 "committed": (out / OUTPUTS_HASH).read_text().strip(),
                 "now": sha256_file(out / OUTPUTS)})
    for r in rows:
        r["match"] = r["committed"] == r["now"]
    return all(r["match"] for r in rows), rows


def to_run_result(outputs: dict, truth: list[dict]):
    """Map the blind outputs onto the planted scenarios (same rules as evals/harness.py)."""
    from evals.schema import MomentResult, RunResult, ScenarioResult

    alerts = [Alert(**a) for a in outputs["alerts"]]
    by_id = {a.draft.id: a for a in alerts}
    chased, bank_held = set(outputs["chased_invoices"]), set(outputs["bank_change_held"])
    scenarios = []
    for t in truth:
        inv, mgr = t.get("invoice_id"), t.get("manager_id")
        if inv:
            mine = [a for a in alerts if inv in a.draft.invoice_ids
                    and a.draft.kind != AlertKind.recurring_pattern]
            mgr = mgr or outputs["invoice_manager"].get(inv)
        else:
            mine = [a for a in alerts if a.draft.manager_id == mgr
                    and a.draft.kind == AlertKind.recurring_pattern]
        kinds = {a.draft.kind for a in mine}
        actions = (["start_chase"] if inv in chased else []) \
            + (["draft_document_request"] if AlertKind.paperwork_request in kinds else []) \
            + (["recommend_stricter_terms"] if AlertKind.recurring_pattern in kinds else []) \
            + (["hold_for_identity_verification", "require_controller_approval"]
               if inv in bank_held else [])
        f = outputs["findings"].get(inv) if inv and outputs["status"].get(inv) in ("delayed", "paid") \
            else None
        recon = outputs["recon"].get(mgr, {"diff": 0.0})
        scenarios.append(ScenarioResult(
            scenario_id=t["scenario"],
            cause=Cause(f["cause"]) if f else None,
            cause_citations=[Citation(**c) for c in f["citations"]] if f else [],
            alerts=mine, actions=actions,
            prohibited_attempted=[f"chase held: {h['policy']}" for h in outputs["chase_held"]
                                  if h["manager_id"] == mgr],
            recon_diff=round(recon["diff"] - recon.get("duplicate_funded", 0.0), 2)))
    before = [by_id[i] for i in outputs["open_before_moment"]
              if by_id[i].draft.manager_id == "keyway"
              and by_id[i].draft.kind == AlertKind.unexplained_unpaid]
    open_now = {a.draft.id for a in alerts if a.state == "open"}
    moment = MomentResult(rechecked=outputs["moment_rechecked"],
                          withdrawn=[by_id[i] for i in outputs["moment_withdrawn"]],
                          kept=[a for a in before if a.draft.id in open_now])
    return RunResult(system="sealed", scenarios=scenarios, moment=moment)


def reveal(out: Path) -> dict:
    out = Path(out)
    ok, rows = verify_hashes(out)
    outputs = json.loads((out / OUTPUTS).read_text())
    report = {"hashes_match": ok, "hash_checks": rows,
              "blocked_attempts": outputs.get("blocked_attempts", []),
              "run_error": outputs.get("error")}
    valid = ok and not report["blocked_attempts"] and not report["run_error"]
    report["valid"] = valid
    if valid:
        from evals.scorer import load_key, score
        s = score(to_run_result(outputs, json.loads((out / TRUTH).read_text())), load_key())
        report["scenarios"] = [{"id": x.id, "name": x.name, "passed": x.passed,
                                "failures": x.failures} for x in s.scenarios]
        report["failures"] = s.failures
        report["metrics"] = s.metrics
        report["passed"] = all(x.passed for x in s.scenarios) and not s.failures
    (out / REPORT).write_text(json.dumps(report, indent=1, default=str))
    return report
