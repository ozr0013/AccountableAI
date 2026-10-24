"""End-to-end eval: generate all 9 scenarios, run Kanishka's workflow on them, play the judge
moment, turn what happened into a RunResult, and score it.

    python -m evals.harness                       # full system vs. no verifier, print results
    python -m evals.harness --out evals/results.md --save evals/runs

No API key -> Kanishka's agents run in rules mode and the verifier runs its code rules only
(contracts.fakes.FakeVerifier). With ANTHROPIC_API_KEY set:
    LIVE=verifier   only the verifier calls the model (cheap: the with/without-verifier comparison)
    LIVE=1          agents and verifier both call the model (slow, much more expensive)
--budget caps verifier spend: the run stops before a call once the cap is reached.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import UTC, datetime
from datetime import time as dtime
from pathlib import Path

from contracts.fakes import FakeApprovals, FakeVerifier
from contracts.models import AlertKind, Event, Verdict, VerdictStatus
from workflow import Deps, Workflow
from workflow.book import WorkflowBook
from workflow.memrails import MemoryRails
from workflow.run import scan_events
from world import generate
from world.generator import ALL_SCENARIOS, MOMENT

from .schema import MomentResult, RunResult, ScenarioResult
from .scorer import score, to_markdown

# $ per million tokens (input, output), for the cost metric.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0),
          "claude-haiku-5-5": (0.10, 0.50)}
_CAUSE_LINE = re.compile(r"^(INV-[\w-]+): (\w+)(?:, cause (\w+))?")


class ApproveAll:
    """Baseline: the system without the verifier. Every draft goes straight to the controller."""

    def verify(self, draft, evidence):
        return Verdict(alert_id=draft.id, status=VerdictStatus.approved, reasons=["no verifier"])


def _live() -> bool:
    """Verifier calls the model."""
    return bool(os.getenv("ANTHROPIC_API_KEY")) and os.getenv("LIVE") in ("1", "verifier")


def _live_agents() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY")) and os.getenv("LIVE") == "1"


def _agents():
    from agents import RealAgents
    if _live_agents():
        from agents.llm import default_llm
        return RealAgents(default_llm())
    return RealAgents(None)


def _verifier(system: str):
    if system == "no_verifier":
        return ApproveAll()
    if _live():
        from agents.verifier import LLMVerifier
        return LLMVerifier()
    return FakeVerifier()


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    """Spend shared by every system in one harness run."""

    def __init__(self, limit_usd: float, max_calls: int | None = None):
        self.limit, self.max_calls, self.spent, self.calls = limit_usd, max_calls, 0.0, 0

    def check(self):
        if self.spent >= self.limit:
            raise BudgetExceeded(f"budget ${self.limit:.2f} reached after {self.calls} calls")
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise BudgetExceeded(f"stopped after {self.calls} calls (--max-calls)")


class _MeteredVerifier:
    """Wraps a verifier to add up model tokens for the cost metric and enforce the budget."""

    def __init__(self, inner, budget: Budget):
        self.inner, self.budget, self.cost = inner, budget, 0.0

    def verify(self, draft, evidence):
        live = not isinstance(self.inner, (ApproveAll, FakeVerifier))
        if live:
            self.budget.check()
        v = self.inner.verify(draft, evidence)
        m = v.meta
        if "input_tokens" in m:
            pin, pout = PRICES.get(m.get("model", ""), (4.0, 20.0))
            c = (m["input_tokens"] * pin + m["output_tokens"] * pout) / 1e6
            self.cost += c
            self.budget.spent += c
            self.budget.calls += 1
            print(f"  verifier call {self.budget.calls}: {v.status.value:8} ${c:.4f} "
                  f"(total ${self.budget.spent:.4f})", file=sys.stderr, flush=True)
        return v


class _CapturingAgents:
    """Wraps the agents to keep the latest CauseFinding per invoice (cause + its citations)."""

    def __init__(self, inner):
        self.inner, self.findings = inner, {}

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def classify_cause(self, line, trail, terms):
        f = self.inner.classify_cause(line, trail, terms)
        self.findings[line.invoice_id] = f
        return f


def _cause_for_paid(rails, agents, manager_id: str, invoice_id: str):
    from workflow.terms import terms_for
    line = next((l for l in rails.ledger(manager_id) if l.invoice_id == invoice_id), None)
    terms = terms_for(rails, agents, manager_id)
    if line is None or terms is None:
        return None
    trail = agents.build_trail(line, rails.documents(manager_id, invoice_id))
    return agents.classify_cause(line, trail, terms)


def _moment_events(as_of) -> list[Event]:
    """The judge moment, as the Judge Console would emit it: first a bare 'we paid the
    electrician' reply (must not change anything), then the real fact (invoice arrived Apr 1)."""
    now = datetime.combine(as_of, dtime(12), tzinfo=UTC)
    inv = MOMENT["fact_key"].split(":")[1].split(".")[0]
    reply = Event(id="ev-judge-moment-reply", type="chase_reply", manager_id="keyway", invoice_id=inv,
                  valid_from=now, learned_at=now,
                  payload={"id": "ev-judge-moment-reply", "manager_id": "keyway", "invoice_ids": [inv],
                           "text": "We paid the electrician.", "received_at": now.isoformat(),
                           "claims_paid": True, "proof": []})
    day = datetime.fromisoformat(MOMENT["value"]).replace(tzinfo=UTC)
    fact = Event(id="ev-judge-moment-fact", type="fact_corrected", manager_id="keyway", invoice_id=inv,
                 valid_from=day, learned_at=now + (datetime.min.replace(minute=1) - datetime.min),
                 payload={"fact_key": MOMENT["fact_key"], "value": MOMENT["value"]})
    return [reply, fact]


def run_system(system: str, n_clean: int = 30, seed: int = 13,
               budget: Budget | None = None) -> RunResult:
    world = generate(seed, ALL_SCENARIOS, n_clean)
    rails = MemoryRails()
    verifier = _MeteredVerifier(_verifier(system), budget or Budget(float("inf")))
    agents = _CapturingAgents(_agents())
    wf = Workflow(Deps(rails, agents, verifier, FakeApprovals()))

    start = time.monotonic()
    results = wf.replay(world.events() + scan_events([m.id for m in world.managers], world.as_of))
    before = WorkflowBook.load(rails)
    open_before = set(before.open_ids)
    reply, fact = _moment_events(world.as_of)
    results += wf.replay([reply, fact])
    elapsed = time.monotonic() - start
    book = WorkflowBook.load(rails)

    # Latest status per invoice from the workflow's trace; a cause only counts while it's delayed.
    status: dict[str, str] = {}
    for r in results:
        for line in r.trace:
            if m := _CAUSE_LINE.match(line):
                status[m.group(1)] = m.group(2)

    chased = {i for msgs in book.chases.values() for c in msgs for i in c.invoice_ids}
    held = [e for e in rails.events() if e.type == "chase_held"]
    bank_held = {e.invoice_id for e in rails.events() if e.type == "bank_change_held"}
    all_alerts = list(book.alerts.values())

    scenarios = []
    for t in world.truth:
        inv, mgr = t.get("invoice_id"), t.get("manager_id")
        if inv:
            # Manager-wide pattern alerts list their invoices as context; they belong to scenario 4.
            alerts = [a for a in all_alerts if inv in a.draft.invoice_ids
                      and a.draft.kind != AlertKind.recurring_pattern]
            mgr = mgr or next((m.id for m in world.managers
                               if any(l.invoice_id == inv for l in rails.ledger(m.id))), None)
        else:
            alerts = [a for a in all_alerts if a.draft.manager_id == mgr
                      and a.draft.kind == AlertKind.recurring_pattern]
        actions = []
        if inv in chased:
            actions.append("start_chase")
        kinds = {a.draft.kind for a in alerts}
        if AlertKind.paperwork_request in kinds:
            actions.append("draft_document_request")
        if AlertKind.recurring_pattern in kinds:
            actions.append("recommend_stricter_terms")
        if inv in bank_held:
            actions += ["hold_for_identity_verification", "require_controller_approval"]
        finding = agents.findings.get(inv) if inv and status.get(inv) == "delayed" else None
        if inv and finding is None and status.get(inv) == "paid":
            # The workflow doesn't ask the cause agent about invoices that are already paid on
            # time (nothing to explain). To grade cause accuracy on every planted item, ask the
            # same agent here, with the same evidence the workflow would give it.
            finding = _cause_for_paid(rails, agents, mgr, inv)
        recon = rails.reconcile(mgr) if mgr else {"diff": 0.0}
        scenarios.append(ScenarioResult(
            scenario_id=t["scenario"],
            cause=finding.cause if finding else None,
            cause_citations=finding.citations if finding else [],
            alerts=alerts,
            actions=actions,
            prohibited_attempted=[f"chase held: {e.payload.get('policy')}" for e in held
                                  if e.manager_id == mgr],
            # A funded duplicate is a flagged, explained difference (scenario 8), not a books error.
            recon_diff=round(recon["diff"] - recon.get("duplicate_funded", 0.0), 2),
            latency_s=elapsed / len(world.truth),
        ))
    scenarios[0].cost_usd = round(verifier.cost, 4)

    keyway_before = {i for i in open_before if before.alerts[i].draft.manager_id == "keyway"
                     and before.alerts[i].draft.kind in (AlertKind.unexplained_unpaid,)}
    fact_result = results[-1]
    moment = MomentResult(
        rechecked=sorted(set(rails.dependents_of(fact.id)) | {fact.invoice_id}),
        withdrawn=[book.alerts[i] for i in fact_result.withdrawn],
        kept=[book.alerts[i] for i in sorted(keyway_before) if i in book.open_ids],
    )
    return RunResult(system=system, scenarios=scenarios, moment=moment)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run all 9 scenarios through the workflow and score them.")
    p.add_argument("--systems", nargs="+", default=["full", "no_verifier"])
    p.add_argument("--clean", type=int, default=30, help="clean background invoices")
    p.add_argument("--out", type=Path, help="write the markdown table here")
    p.add_argument("--save", type=Path, help="save each RunResult JSON in this folder")
    p.add_argument("--budget", type=float, default=2.0, help="max verifier spend in USD (live)")
    p.add_argument("--max-calls", type=int, help="stop after this many model calls (cost probe)")
    a = p.parse_args(argv)

    budget = Budget(a.budget, a.max_calls)
    try:
        runs = [run_system(s, a.clean, budget=budget) for s in a.systems]
    except BudgetExceeded as e:
        print(f"STOPPED: {e}. Spent ${budget.spent:.4f} on {budget.calls} calls "
              f"(${budget.spent / max(budget.calls, 1):.4f} per call).")
        return 2
    if a.save:
        a.save.mkdir(parents=True, exist_ok=True)
        for r in runs:
            (a.save / f"{r.system}.json").write_text(r.model_dump_json(indent=2))
    md = to_markdown([score(r) for r in runs])
    if _live_agents():
        mode = "live: agents and verifier call the model"
    elif _live():
        mode = (f"live verifier ({os.getenv('VERIFIER_MODEL', 'default model')}), agents in rules mode; "
                f"{budget.calls} verifier calls, ${budget.spent:.4f}")
    else:
        mode = "no API key: agents in rules mode, verifier code rules only"
    md = md.replace("# Eval results\n", f"# Eval results\n\nMode: {mode}.\n", 1)
    if a.out:
        a.out.write_text(md)
        print(f"wrote {a.out}")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
