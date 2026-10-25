"""The 15-day simulation on Live investigation: one script, run step by step by the real session.

The page asks the feed to run each step (POST /api/simulation/{i}) and animates what came back:
the workflow steps that ran, the agents that ran and every write to the event log. With no feed
running, it plays a recording of a real run:

    python dashboard/feed/simulation.py       # record dashboard/public/feed/simulation.json

tests/test_simulation.py runs the same script and checks the numbers, so the page can't show a
flow the backend doesn't produce.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
SAMPLES = HERE / "samples"

# Day 1 is the demo date, Apr 22, 2026.
SCRIPT = [
    {"day": 1, "kind": "file", "arg": "Keyway_Owner_Statement_Apr2026.pdf", "label": "Keyway's April statement arrives"},
    {"day": 1, "kind": "file", "arg": "Keyway_Reply_KW-0901.eml", "label": "Keyway replies: “Brightside was paid”"},
    {"day": 2, "kind": "clock", "arg": 1, "label": "A day passes"},
    {"day": 2, "kind": "fact", "arg": "invoice_received_correction", "label": "Fact: the electrician's invoice arrived Apr 15"},
    {"day": 4, "kind": "clock", "arg": 2, "label": "Two days pass"},
    {"day": 4, "kind": "file", "arg": "RioGrande_Payment_Received.eml", "label": "Rio Grande confirms it was paid"},
    {"day": 7, "kind": "clock", "arg": 3, "label": "Three days pass"},
    {"day": 7, "kind": "file", "arg": "Keyway_Bank_Export_Apr22.csv", "label": "Keyway's bank export arrives"},
    {"day": 12, "kind": "clock", "arg": 5, "label": "Five days pass"},
    {"day": 15, "kind": "clock", "arg": 3, "label": "Three days pass"},
]


def run(session, i: int) -> dict:
    """Run step i on the session; the result is the session's usual action result."""
    step = SCRIPT[i]
    if step["kind"] == "file":
        out = session.add_file(SAMPLES / step["arg"])
    elif step["kind"] == "clock":
        out = session.advance(step["arg"])
    else:
        out = session.console(step["arg"])
    return {**out, "index": i, "day": step["day"], "label": step["label"], "source": step["kind"]}


def record() -> Path:
    """Run the whole script on a fresh session and save what the page needs to replay it."""
    from session import DemoSession
    s = DemoSession()
    start = s.snapshot()["headline"]["unexplained"]
    frames = []
    for i in range(len(SCRIPT)):
        r = run(s, i)
        frames.append({k: r.get(k) for k in ("index", "day", "label", "source", "kind", "summary", "steps",
                                             "agents", "writes", "ok")}
                      | {"unexplained": r["state"]["headline"]["unexplained"],
                         "as_of": r["state"]["meta"]["as_of"], "diff": {"unexplained": r["diff"]["unexplained"]}})
    out = HERE.parent / "public" / "feed" / "simulation.json"
    out.write_text(json.dumps({"start": start, "frames": frames}, indent=1) + "\n")
    return out


if __name__ == "__main__":
    sys.path[:0] = [str(HERE.parent.parent), str(HERE)]
    print(f"recorded {record()}")
