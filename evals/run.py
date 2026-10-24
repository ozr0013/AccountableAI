"""Score one or more saved runs and write a comparison table.

    python -m evals.run evals/runs/full.json evals/runs/no_verifier.json --out evals/results.md
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .schema import RunResult
from .scorer import score, to_markdown


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("runs", nargs="+", type=Path, help="RunResult JSON files")
    p.add_argument("--out", type=Path, help="write markdown here (default: print)")
    args = p.parse_args(argv)

    scores = [score(RunResult.model_validate_json(f.read_text())) for f in args.runs]
    md = to_markdown(scores)
    if args.out:
        args.out.write_text(md)
        print(f"wrote {args.out}")
    else:
        print(md)
    return 1 if any(s.metrics["prohibited_executed"] for s in scores) else 0


if __name__ == "__main__":
    raise SystemExit(main())
