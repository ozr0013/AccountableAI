"""Replay the demo world through the workflow, then ask the briefing agent a question.

    python -m agents.briefing "What's open with Keyway?"            # code briefing, free
    python -m agents.briefing "What's open with Keyway?" --model     # one Claude call (Sonnet)
"""

from __future__ import annotations

import argparse

from contracts.fakes import FakeApprovals, FakeVerifier
from workflow import Deps, Workflow
from workflow.memrails import MemoryRails
from workflow.run import scan_events
from world import generate
from world.generator import ALL_SCENARIOS

from .briefer import Briefer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?", default="What's open with Keyway?")
    ap.add_argument("--manager", default="keyway")
    ap.add_argument("--model", action="store_true", help="use the model (costs one API call)")
    a = ap.parse_args()

    from agents import RealAgents
    world = generate(13, ALL_SCENARIOS, 30)
    rails = MemoryRails()
    wf = Workflow(Deps(rails, RealAgents(None), FakeVerifier(), FakeApprovals()))
    wf.replay(world.events() + scan_events([m.id for m in world.managers], world.as_of))

    briefer = Briefer(rails, use_model=a.model)
    b = briefer.brief(a.manager, a.question)
    print(f"Q: {b.question}\n\n{b.answer}\n")
    for c in b.claims:
        print(f"- {c.text}  [{', '.join(x.source_id for x in c.citations)}]")
    if b.prior_rulings:
        print("\nPrior rulings:")
        for r in b.prior_rulings:
            print(f"- {r}")
    m = briefer.last
    print(f"\n({m.get('mode')}"
          + (f", {m['model']}, {m['input_tokens']} in / {m['output_tokens']} out tokens" if "model" in m else "")
          + (f", dropped {len(m['dropped_claims'])} claim(s)" if m.get("dropped_claims") else "")
          + (f"; {m['why']}" if m.get("why") else "") + ")")


if __name__ == "__main__":
    main()
