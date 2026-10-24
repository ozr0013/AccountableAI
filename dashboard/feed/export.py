"""Replay feed (NEXT_PUBLIC_FEED=replay): bake the demo session into static JSON.

    python dashboard/feed/export.py           # writes dashboard/public/feed/*.json

state.json      the dashboard on Apr 22, before the judge speaks
moment.json     the Judge Console preset `invoice_received_correction` and the state after it
briefing*.json  the code briefing for the preset questions, before and after the moment
presets.json    Judge Console presets (replay mode can only play the moment)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[1]), str(HERE)]

from session import DemoSession

OUT = HERE.parent / "public" / "feed"
QUESTIONS = ["What's open with Keyway?", "Why are we holding money from Keyway?",
             "Is any of this not Keyway's fault?"]


def _write(name: str, data) -> None:
    (OUT / name).write_text(json.dumps(data, indent=1, sort_keys=False, default=str))
    print(f"wrote {OUT / name}")


def main() -> None:
    from rails.console.console import PRESETS
    OUT.mkdir(parents=True, exist_ok=True)
    s = DemoSession()
    _write("state.json", s.snapshot())
    _write("briefing.json", {q: s.brief(q) for q in QUESTIONS})
    _write("presets.json", [{"id": p.id, "label": p.label, "description": p.description,
                             "params": p.params, "defaults": p.defaults} for p in PRESETS])
    m = s.console("invoice_received_correction")
    _write("moment.json", m)
    _write("briefing_after.json", {q: s.brief(q) for q in QUESTIONS})
    h = m["diff"]["unexplained"]
    print(f"moment: ${h[0]:,.2f} -> ${h[1]:,.2f}; withdrew "
          + ", ".join(a["id"] for a in m["diff"]["withdrawn"]))


if __name__ == "__main__":
    main()
