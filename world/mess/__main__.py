"""python -m world.mess --out out/raw [--seed 13] [--clean 100] [--scenarios 1 ... 9]"""

import argparse
from collections import Counter
from pathlib import Path

from world.generator import ALL_SCENARIOS, generate

from .render import render


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--clean", type=int, default=100)
    ap.add_argument("--scenarios", type=int, nargs="*", default=list(ALL_SCENARIOS))
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    w = generate(a.seed, tuple(a.scenarios), a.clean)
    out = render(w, a.out)
    by_dir = Counter(f.path.split("/")[0] for f in out.files)
    tags = Counter(t for f in out.files for t in f.noise)
    print(f"{len(out.files)} raw files -> {a.out}  " + ", ".join(f"{k}: {v}" for k, v in sorted(by_dir.items())))
    print("mess: " + ", ".join(f"{k} x{v}" for k, v in tags.most_common()))


main()
