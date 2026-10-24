"""python -m world --seed 13 --out out/world [--clean 100] [--scenarios 3]"""

import argparse
import json
from pathlib import Path

from .generator import generate


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--clean", type=int, default=100)
    ap.add_argument("--scenarios", type=int, nargs="*", default=[3])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    w = generate(a.seed, tuple(a.scenarios), a.clean)
    a.out.mkdir(parents=True, exist_ok=True)
    dump = lambda name, rows: (a.out / name).write_text(
        json.dumps(rows, indent=2, default=str) + "\n")
    dump("world.json", w.meta())
    dump("documents.json", [d.model_dump(mode="json") for d in w.documents])
    dump("ledger.json", [l.model_dump(mode="json") for l in w.ledger])
    dump("truth.json", w.truth)
    print(f"{len(w.documents)} documents, {len(w.ledger)} ledger lines -> {a.out}")


main()
