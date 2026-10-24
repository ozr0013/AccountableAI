"""Score the parser against the truth: render the world as messy raw files, read them back,
compare every field with the clean documents they came from.

    python -m evals.extraction                    # print the report
    python -m evals.extraction --out evals/extraction_results.md --raw out/raw

A field counts only if it matches exactly (amounts to the cent, dates to the day, ids exact).
"Flagged" means the parser refused to guess and asked a person: for a scan with no OCR that is
the right answer, not a miss.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from agents.extract import IngestResult, ingest_folder
from world.generator import ALL_SCENARIOS, generate
from world.mess import render

SKIP_KEYS = {"reply_id"}


def _norm(v):
    if isinstance(v, float):
        return round(v, 2)
    return v


def _cmp_list(truth: list[dict], got: list[dict]) -> tuple[int, int]:
    keys = sorted({k for r in truth for k in r})
    t = Counter(tuple(_norm(r.get(k)) for k in keys) for r in truth)
    g = Counter(tuple(_norm(r.get(k)) for k in keys) for r in got)
    hit = sum((t & g).values())
    return hit, max(sum(t.values()), sum(g.values()))


def docs_for_file(res: IngestResult, path: str):
    return [d for d in res.documents if d.id == f"raw:{path}" or d.id.startswith(f"raw:{path}#")
            or d.id.startswith(f"raw:{path}/")]


def records_for_file(res: IngestResult, path: str):
    return [p for p in res.records if p.source == path or p.source.startswith(path + "/")]


def score(manifest: dict, res: IngestResult) -> dict:
    truth = manifest["truth_docs"]
    fields_ok = fields_all = 0
    docs_exact = docs_total = 0
    by_kind: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_noise: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    misses: list[str] = []
    flagged_ok = flagged_total = 0
    ignored_ok = ignored_total = 0
    mapping: dict[str, list[str]] = {}
    for f in manifest["files"]:
        got = docs_for_file(res, f["path"])
        recs = records_for_file(res, f["path"])
        if f["expect"] == "ignore":
            ignored_total += 1
            ignored_ok += int(not got)
            continue
        if f["expect"] == "needs_human_without_ocr" and not got:
            flagged_total += 1
            flagged_ok += int(any(p.needs_human for p in recs))
            for tid in f["truth"]:
                mapping[tid] = []
            continue
        if "duplicate_forward" in f["noise"]:
            ignored_total += 1
            ignored_ok += int(not got)
            continue
        for tid in f["truth"]:
            t = truth[tid]
            if t["kind"] == "agreement":
                cands = [d for d in got if d.kind.value == "agreement"]
            else:
                inv = t["data"].get("invoice_id")
                cands = [d for d in got if d.kind.value == t["kind"]
                         and (len(f["truth"]) == 1 or d.data.get("invoice_id") == inv)]
            d = cands[0] if cands else None
            mapping[tid] = [d.id] if d else []
            checks: list[tuple[str, bool, int]] = []
            if d is None:
                checks.append(("found", False, 1))
            else:
                checks.append(("kind", True, 1))
                if t["vendor_id"]:
                    checks.append(("vendor_id", d.vendor_id == t["vendor_id"], 1))
                checks.append(("dated", (d.dated.isoformat() if d.dated else None) == t["dated"], 1))
                for k, v in t["data"].items():
                    if k in SKIP_KEYS:
                        continue
                    if isinstance(v, list):
                        hit, n = _cmp_list(v, d.data.get(k, []))
                        checks.append((k, hit == n, n or 1))
                        if n:
                            fields_ok += hit
                            fields_all += n
                        continue
                    checks.append((k, _norm(d.data.get(k)) == _norm(v), 1))
            ok = all(c[1] for c in checks)
            for name, good, n in checks:
                if not isinstance(t["data"].get(name), list):
                    fields_ok += int(good)
                    fields_all += 1
            docs_total += 1
            docs_exact += int(ok)
            by_kind[t["kind"]][0] += int(ok)
            by_kind[t["kind"]][1] += 1
            for tag in f["noise"] or ["(none)"]:
                by_noise[tag][0] += int(ok)
                by_noise[tag][1] += 1
            if not ok:
                bad = [c[0] for c in checks if not c[1]]
                misses.append(f"{tid} ({f['path']}): {', '.join(bad)}")
    methods = Counter(p.method for p in res.records)
    return dict(docs_exact=docs_exact, docs_total=docs_total, fields_ok=fields_ok,
                fields_all=fields_all, by_kind=dict(by_kind), by_noise=dict(by_noise),
                misses=misses, flagged_ok=flagged_ok, flagged_total=flagged_total,
                ignored_ok=ignored_ok, ignored_total=ignored_total, mapping=mapping,
                needs_human=len(res.needs_human), methods=dict(methods),
                raw_files=len(manifest["files"]))


def to_markdown(s: dict, res: IngestResult) -> str:
    pct = lambda a, b: f"{100 * a / b:.1f}%" if b else "n/a"
    lines = ["# Extraction results: messy raw files back to documents", "",
             f"{s['raw_files']} raw files (emails with PDF attachments, scans, bank CSVs, wire "
             f"PDFs, statements, junk mail). Rules-first parser, no model calls unless a scan "
             f"needs OCR.", "",
             "| Metric | Result |", "|---|---|",
             f"| Documents fully correct (every field) | {s['docs_exact']}/{s['docs_total']} "
             f"({pct(s['docs_exact'], s['docs_total'])}) |",
             f"| Fields correct | {s['fields_ok']}/{s['fields_all']} ({pct(s['fields_ok'], s['fields_all'])}) |",
             f"| Junk and duplicate copies correctly ignored | {s['ignored_ok']}/{s['ignored_total']} |",
             f"| Unreadable scans flagged for a person (no OCR) | {s['flagged_ok']}/{s['flagged_total']} |",
             f"| Records flagged for a person in total | {s['needs_human']} |",
             f"| How text was read | {', '.join(f'{k}: {v}' for k, v in sorted(s['methods'].items()))} |",
             "", "## By document type", "", "| Type | Fully correct |", "|---|---|"]
    lines += [f"| {k} | {a}/{b} |" for k, (a, b) in sorted(s["by_kind"].items())]
    lines += ["", "## By kind of mess", "", "| Mess | Fully correct |", "|---|---|"]
    lines += [f"| {k} | {a}/{b} |" for k, (a, b) in sorted(s["by_noise"].items())]
    lines += ["", "## Misses", ""] + ([f"- {m}" for m in s["misses"]] or ["None."])
    flags = [f"- `{p.source}`: {'; '.join(p.warnings)}" for p in res.needs_human]
    lines += ["", "## What the parser asked a person about", ""] + (flags or ["Nothing."])
    return "\n".join(lines) + "\n"


def run(seed: int = 13, n_clean: int = 100, raw: Path | None = None):
    w = generate(seed, ALL_SCENARIOS, n_clean)
    tmp = None
    if raw is None:
        tmp = Path(tempfile.mkdtemp(prefix="raw-"))
        raw = tmp
    render(w, raw)
    manifest = json.loads((raw / "manifest.json").read_text())
    res = ingest_folder(raw, w.vendors)
    s = score(manifest, res)
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return w, res, s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--clean", type=int, default=100)
    ap.add_argument("--raw", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    _, res, s = run(a.seed, a.clean, a.raw)
    md = to_markdown(s, res)
    if a.out:
        a.out.write_text(md)
    print(md)


if __name__ == "__main__":
    main()
