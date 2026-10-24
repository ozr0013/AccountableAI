"""python -m workflow.sealed [--dir DIR] [--seed 13] [--n-clean 30]

Seal -> blind run in a separate Python process -> reveal. Exit code 0 only if every hash
matches, nothing tried to read the answers, and every scenario passes.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from .common import REPO
from .reveal import reveal
from .seal import seal


def _short(h: str) -> str:
    return h[:16] if len(h) > 16 else h


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dir", type=Path, help="where to keep the sealed run (default: a temp dir)")
    p.add_argument("--seed", type=int, default=13)
    p.add_argument("--n-clean", type=int, default=30)
    a = p.parse_args(argv)
    out = a.dir or Path(tempfile.mkdtemp(prefix="sealed-eval-"))

    c = seal(out, a.seed, a.n_clean)
    print(f"STEP 1  sealed {len(c['sealed_files'])} answer files "
          f"(commitment {_short(c['commitment_sha256'])}) -> {out}")
    run = subprocess.run([sys.executable, "-m", "workflow.sealed.blind_run", str(out)],
                         check=False, cwd=REPO, capture_output=True, text=True)
    print(f"STEP 2  blind run in a separate process: exit {run.returncode}")
    if run.stderr.strip():
        print("        " + run.stderr.strip().splitlines()[-1])
    r = reveal(out)
    print("STEP 3  re-hashed and compared:")
    for row in r["hash_checks"]:
        print(f"        {'OK  ' if row['match'] else 'FAIL'} {row['what']:<36} "
              f"{_short(row['committed'])} -> {_short(row['now'])}")
    print(f"        answer reads/imports blocked during the run: {r['blocked_attempts'] or 'none'}")
    if not r["valid"]:
        print("RESULT  INVALID: " + ("hash mismatch" if not r["hashes_match"]
                                     else r["run_error"] or "the run tried to read the answers"))
        return 2
    print("STEP 4  graded against the answer key (only after the hashes matched):")
    for s in r["scenarios"]:
        print(f"        scenario {s['id']} {s['name']:<28} "
              f"{'PASS' if s['passed'] else 'FAIL ' + '; '.join(s['failures'])}")
    for f in r["failures"]:
        print(f"        FAIL {f}")
    print(f"RESULT  {'PASS' if r['passed'] else 'FAIL'} (report: {out / 'report.json'})")
    return 0 if r["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
