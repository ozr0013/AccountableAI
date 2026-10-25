"""Score a pack run's report against the pack's answer key. Free, deterministic.

    python world/pack/score.py world/pack/run_sonnet_2026-10-24
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _alerts(rep: dict, kind: str, needle: str) -> list[dict]:
    return [a for a in rep["open_alerts"] if a["kind"] == kind and needle.lower() in a["headline"].lower()]


def _cause(rep: dict, iid: str) -> str | None:
    found = None
    for line in rep["causes"]:
        if line.startswith(f"{iid}:") and ", cause " in line:
            found = line.split(", cause ")[1].split()[0]
    return found


def _reply_held(rep: dict, event: str) -> bool:
    return any(t["event"] == event and any("holding position" in x for x in t["trace"]) for t in rep.get("traces", []))


def check(rep: dict, test: dict) -> bool:
    k, v = next(iter(test.items()))
    if k == "terms":
        return (all(float(rep["terms"][a]) == float(b) for a, b in v.items())
                and all(rep["term_clauses"].get(a) == b for a, b in test["clauses"].items()))
    if k == "open_alert":
        return bool(_alerts(rep, *v))
    if k == "no_open_alert":
        return not _alerts(rep, *v)
    if k == "reply_held":
        return _reply_held(rep, v)
    if k == "quarantined":
        return any(q.startswith(v) for q in rep["notes"]["quarantined"])
    if k == "flag":
        return any(all(x in f for x in v) for f in rep["notes"]["flags"])
    if k == "cause":
        return _cause(rep, v[0]) == v[1]
    if k == "paid_no_alert":
        line = next((ln for ln in rep["ledger"] if ln["invoice_id"] == v), None)
        return bool(line and line["status"] == "paid" and not any(v in a["headline"] for a in rep["open_alerts"]))
    if k == "duplicate":
        return any(ln["invoice_id"] == v and ln.get("duplicate_of") for ln in rep["ledger"]) or bool(_alerts(rep, "duplicate_billing", v))
    if k == "recon_diff":
        return abs(rep["recon"]["diff"] - v) < 0.005
    raise ValueError(f"unknown test {k}")


def score(run_dir: Path, key_path: Path = HERE / "answer_key.json") -> dict:
    rep = json.loads((run_dir / "report.json").read_text())
    key = json.loads(key_path.read_text())
    results = [{"id": c["id"], "what": c["what"], "expect": c["expect"], "passed": check(rep, c["test"])} for c in key["checks"]]
    return {"pack": key["pack"], "passed": sum(r["passed"] for r in results), "total": len(results), "checks": results}


if __name__ == "__main__":
    out = score(Path(sys.argv[1]))
    for r in out["checks"]:
        print(f"  {'PASS' if r['passed'] else 'FAIL'}  {r['id']:18s} {r['expect']}")
    print(f"  {out['passed']} / {out['total']}")
