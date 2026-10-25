"""The engine end to end on messy paperwork: raw files in, alerts and a hold recommendation out.

    python -m workflow.engine                    # free: no model calls, even with a key in .env
    python -m workflow.engine --raw out/raw      # keep the raw files to open them
    python -m workflow.engine --live             # models read text (agreement, replies,
                                                 # statements, unclear scans) and the verifier
                                                 # tries to refute each alert; costs money

The demo world is rendered as what Oakline's controller receives (PDFs, scans, emails, bank
CSVs), parsed back by agents/extract, and only the parsed documents reach the workflow. The
generator's ledger facts, agreement terms and vendor master are kept: those are what the
controller already knows (see evals/messy.py).

Four agents: agreement, chaser and anomaly read human text; the verifier checks every alert.
Trail, cause and case are code.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime, time
from pathlib import Path

from contracts.fakes import FakeApprovals, FakeVerifier
from contracts.models import AlertKind, Event
from world import generate
from world.generator import ALL_SCENARIOS

from .book import WorkflowBook
from .memrails import MemoryRails
from .run import scan_events
from .runner import Workflow
from .steps import Deps


def _funding(manager_id: str, amount: float, as_of, tag: str) -> Event:
    when = datetime.combine(as_of, time(23, 30), tzinfo=UTC)
    return Event(id=f"ev-funding-{manager_id}-{as_of.isoformat()}-{tag}",
                 type="funding_review_requested", manager_id=manager_id,
                 valid_from=when, learned_at=when, payload={"amount": amount})


def _hold(rails) -> str:
    """The latest funding review's recommendation, e.g. 'Recommend holding $3,140.00 of ...'."""
    case = [e.payload for e in rails.events() if e.type == "case_proposed"][-1]
    return case["recommendation"].split(". ")[0]


def _unexplained(book) -> float:
    return round(sum(a.draft.amount for a in book.open_alerts()
                     if a.draft.kind == AlertKind.unexplained_unpaid), 2)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw", type=Path, help="write the raw files here and keep them")
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--clean", type=int, default=30, help="clean background invoices")
    ap.add_argument("--funding", type=float, default=18400.0, help="Oakline's next funding to Keyway")
    ap.add_argument("--live", action="store_true", help="use the models (needs ANTHROPIC_API_KEY)")
    a = ap.parse_args()

    if a.live and not os.getenv("ANTHROPIC_API_KEY"):
        ap.error("--live needs ANTHROPIC_API_KEY")
    if not a.live:
        os.environ["EXTRACT_LLM"] = "off"      # a key in .env must not mean spending money
    from agents import RealAgents
    from evals.messy import messy_world
    if a.live:
        from agents.llm import default_llm
        from agents.verifier import LLMVerifier
        agents, verifier = RealAgents(default_llm()), LLMVerifier()
        print("LIVE: model calls are billed to your API key.")
    else:
        agents, verifier = RealAgents(None), FakeVerifier()

    world, parsed = messy_world(generate(a.seed, ALL_SCENARIOS, a.clean), a.raw)
    print(f"Read {len(parsed.records)} records from the raw files: {len(parsed.documents)} documents, "
          f"{len(parsed.needs_human)} flagged for a person, {len(parsed.ignored)} ignored (junk)"
          + (f"  [files in {a.raw}]" if a.raw else ""))

    rails = MemoryRails()
    wf = Workflow(Deps(rails, agents, verifier, FakeApprovals()))
    wf.replay(world.events() + scan_events([m.id for m in world.managers], world.as_of))
    wf.replay([_funding("keyway", a.funding, world.as_of, "before")])
    book = WorkflowBook.load(rails)
    print(f"\n{len(book.open_ids)} open alert(s) on {world.as_of}:")
    for al in book.open_alerts():
        print(f"  {al.draft.headline}")
    print(f"Unexplained: ${_unexplained(book):,.2f}. {_hold(rails)}.")

    from rails.console import JudgeConsole
    res = JudgeConsole(rails).apply_preset("invoice_received_correction")
    print(f"\nJudge: {res.summary} -> recheck only {res.affected}")
    for r in wf.replay(res.events):
        for aid in r.withdrawn:
            print(f"  withdrawn {aid}: {WorkflowBook.load(rails).alerts[aid].withdrawn_reason}")
    wf.replay([_funding("keyway", a.funding, world.as_of, "after")])
    book = WorkflowBook.load(rails)
    print(f"Unexplained: ${_unexplained(book):,.2f}. {_hold(rails)}.")


if __name__ == "__main__":
    main()
