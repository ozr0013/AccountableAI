"""Shared bits for the sealed eval: file names, hashing, event (de)serialization."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from contracts.models import Event

REPO = Path(__file__).resolve().parents[2]
ANSWER_FILES = [REPO / "evals" / "answer_key.json", REPO / "world" / "scenario_spec.json"]

INPUTS = "events.jsonl"          # the system's only input
TRUTH = "truth.json"             # the world's planted truth (an answer file; sealed)
COMMITMENT = "commitment.json"   # hashes made before the run
OUTPUTS = "outputs.json"         # what the system did
OUTPUTS_HASH = "outputs.sha256"  # hash made right after the run
REPORT = "report.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_events(path: Path, phases: list[tuple[str, list[Event]]]) -> None:
    with Path(path).open("w") as f:
        for phase, events in phases:
            f.writelines(json.dumps({"phase": phase, "event": e.model_dump(mode="json")},
                                   sort_keys=True) + "\n" for e in events)


def read_events(path: Path) -> dict[str, list[Event]]:
    out: dict[str, list[Event]] = {}
    for line in Path(path).read_text().splitlines():
        row = json.loads(line)
        out.setdefault(row["phase"], []).append(Event(**row["event"]))
    return out
