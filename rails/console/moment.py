"""Replay the demo world through the workflow, then play the judge moment.

    python -m rails.console.moment

Runs in memory (the workflow's MemoryRails), with the agents in rules mode (no API key needed)
and the rule-only verifier. Injects the `invoice_received_correction` preset through the Judge
Console and prints what was rechecked, what was withdrawn and what stays open.
"""

from __future__ import annotations

from dataclasses import dataclass

from contracts.models import AlertKind


@dataclass
class MomentReport:
    rechecked: list[str]
    withdrawn: list[str]              # invoice ids whose unexplained alert was withdrawn
    kept: list[str]                   # invoice ids still open as unexplained and unpaid
    before: dict[str, float]          # invoice id -> amount, unexplained and unpaid
    after: dict[str, float]
    withdrawn_amount: float
    summary: str

    @property
    def total_before(self) -> float:
        return round(sum(self.before.values()), 2)

    @property
    def total_after(self) -> float:
        return round(sum(self.after.values()), 2)


def _unexplained(rails) -> dict[str, float]:
    """Open unexplained alerts that still have money owed (a $0 'paid late' alert is not one)."""
    from workflow.book import WorkflowBook
    book = WorkflowBook.load(rails)
    return {a.draft.invoice_ids[0]: a.draft.amount for a in book.open_alerts("keyway")
            if a.draft.kind == AlertKind.unexplained_unpaid and a.draft.amount > 0}


def run_moment(seed: int = 13, n_clean: int = 100) -> MomentReport:
    from agents import RealAgents
    from contracts.fakes import FakeApprovals, FakeVerifier
    from workflow import Deps, Workflow
    from workflow.book import WorkflowBook
    from workflow.memrails import MemoryRails
    from workflow.run import scan_events
    from world import generate
    from world.generator import ALL_SCENARIOS

    from .console import JudgeConsole

    world = generate(seed, ALL_SCENARIOS, n_clean)
    rails = MemoryRails()
    wf = Workflow(Deps(rails, RealAgents(None), FakeVerifier(), FakeApprovals()))
    wf.replay(world.events() + scan_events(["keyway"], world.as_of))
    before = _unexplained(rails)

    result = JudgeConsole(rails).apply_preset("invoice_received_correction")
    wf_result = wf.handle(result.events[0])
    book = WorkflowBook.load(rails)
    withdrawn = sorted(book.alerts[a].draft.invoice_ids[0] for a in wf_result.withdrawn)
    after = _unexplained(rails)
    return MomentReport(rechecked=result.affected, withdrawn=withdrawn, kept=sorted(after),
                        before=before, after=after,
                        withdrawn_amount=round(sum(before.get(i, 0.0) for i in withdrawn), 2),
                        summary=result.summary)


def main() -> None:
    import logging
    logging.getLogger("langgraph").setLevel(logging.ERROR)
    r = run_moment()
    print(f"Judge fact: {r.summary}")
    print(f"Rechecked (dependents_of): {', '.join(r.rechecked)}")
    print(f"Unexplained before: ${r.total_before:,.2f}  "
          + ", ".join(f"{i} ${a:,.2f}" for i, a in sorted(r.before.items())))
    for i in r.withdrawn:
        print(f"Withdrawn: {i} ${r.before.get(i, 0.0):,.2f}")
    print(f"Kept: " + ", ".join(f"{i} ${a:,.2f}" for i, a in sorted(r.after.items())))
    print(f"${r.total_before:,.2f} -> ${r.total_after:,.2f} "
          f"(withdrew ${r.withdrawn_amount:,.2f})")


if __name__ == "__main__":
    main()
