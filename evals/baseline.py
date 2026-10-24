"""Baseline: one strong model, all the documents, one prompt. No workflow, agents or verifier.

Answers the judges' question "why not just give a big model everything?". The model reads every
Keyway document as of the demo date and returns its findings (cause, alert, actions, sources) for
every item that needs attention. A second turn plays the judge moment (a bare "we paid the
electrician" reply, then the corrected receipt date) with the documents cached. The answers are
mapped to a RunResult and graded by evals/scorer.py exactly like the system.

    python -m evals.baseline --dry-run                 # count tokens, estimate cost (free)
    python -m evals.baseline --budget 1.0 --out evals/baseline_results.md
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Literal

from pydantic import BaseModel

from contracts.models import (
    Alert,
    AlertDraft,
    AlertKind,
    Cause,
    Citation,
    Claim,
    Verdict,
    VerdictStatus,
)
from workflow.memrails import MemoryRails
from world import generate
from world.generator import ALL_SCENARIOS, MOMENT


def _render_evidence(evidence) -> str:
    import json
    out = []
    for d in sorted(evidence, key=lambda d: (str(d.dated or ""), d.id)):
        data = json.dumps(d.data, sort_keys=True) if d.data else "{}"
        out.append(f'<document id="{d.id}" kind="{d.kind.value}" dated="{d.dated or ""}">\n'
                   f"{d.text}\n<data>{data}</data>\n</document>")
    return "\n".join(out)

from .schema import MomentResult, RunResult, ScenarioResult
from .scorer import score, to_markdown

MODEL = os.getenv("BASELINE_MODEL", "claude-opus-5-5")
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20), "claude-sonnet-5-5": (2.0, 10.0, 0.20)}  # in, out, cache read

SYSTEM = """You are Oakline Homes' accounts-payable reviewer. Oakline funds a property manager, \
Keyway Property Management, who must pay vendors from that money under a management agreement. \
You are given every document Oakline has: the agreement, funding records, vendor invoices, the \
manager's monthly statements and bank records, vendor notices and confirmations, and manager replies.

Report a finding for every vendor invoice that is unpaid, partly paid, paid late, held, billed \
twice, or otherwise needs the controller's attention, as of the demo date. Also report one \
manager-level finding (item_id "keyway", alert "recurring_pattern") if Keyway pays vendors late \
in several months, measured against the agreement's payment window.

For each finding give:
- cause: authorized_hold (the agreement allows it), paperwork_pending (vendor owes paperwork), \
vendor_side (on time under the terms, or the delay is the vendor's), owner_side (Oakline hasn't \
funded or approved it), unexplained (no valid reason), or none (for manager-level, duplicate or \
bank-change findings)
- alert: unexplained_unpaid, paperwork_request, owner_pending_approval, bank_detail_change, \
duplicate_billing, recurring_pattern, or none
- actions, from: start_chase, draft_document_request, recommend_stricter_terms, \
hold_for_identity_verification, require_controller_approval
- source_ids: the exact ids of the documents that support it

Source-trust rules: bank records beat the manager's statement; a vendor notice or confirmation \
beats a manager's "paid" claim; a "paid" reply is not proof without a bank record or vendor \
confirmation; the agreement is the source of truth for deadlines, holdbacks and approval limits. \
Use neutral wording; never accuse."""

_CAUSES = Literal["authorized_hold", "paperwork_pending", "vendor_side", "owner_side", "unexplained", "none"]
_ALERTS = Literal["unexplained_unpaid", "paperwork_request", "owner_pending_approval",
                  "bank_detail_change", "duplicate_billing", "recurring_pattern", "none"]
_ACTIONS = Literal["start_chase", "draft_document_request", "recommend_stricter_terms",
                   "hold_for_identity_verification", "require_controller_approval"]


class Finding(BaseModel):
    item_id: str
    cause: _CAUSES
    alert: _ALERTS
    actions: list[_ACTIONS]
    source_ids: list[str]
    why: str


class Findings(BaseModel):
    findings: list[Finding]


def moment_message(as_of) -> str:
    inv = MOMENT["fact_key"].split(":")[1].split(".")[0]
    return (f"New facts, {as_of}:\n"
            f"1. Keyway replied: \"We paid the electrician.\" (no attachment)\n"
            f"2. Correction: the electrician's invoice {inv} was actually received on "
            f"{MOMENT['value']}, not the date recorded earlier.\n\n"
            "Return your full, updated list of findings.")


class Baseline:
    def __init__(self, client=None, model: str = MODEL, budget_usd: float = 1.0):
        if client is None:
            import anthropic
            client = anthropic.Anthropic(timeout=600, max_retries=2)
        self.client, self.model, self.budget = client, model, budget_usd
        self.cost, self.calls, self.latency = 0.0, 0, 0.0

    def _ask(self, messages) -> Findings:
        if self.cost >= self.budget:
            raise RuntimeError(f"budget ${self.budget:.2f} reached")
        start = time.monotonic()
        with self.client.messages.stream(model=self.model, max_tokens=64000, system=SYSTEM,
                                         messages=messages, output_format=Findings) as stream:
            resp = stream.get_final_message()
        self.latency += time.monotonic() - start
        pin, pout, pcache = PRICES.get(self.model, (4.0, 20.0, 0.20))
        u = resp.usage
        cached = getattr(u, "cache_read_input_tokens", 0) or 0
        written = getattr(u, "cache_creation_input_tokens", 0) or 0
        c = (u.input_tokens * pin + written * pin * 1.25 + cached * pcache + u.output_tokens * pout) / 1e6
        self.cost += c
        self.calls += 1
        print(f"  baseline call {self.calls}: {u.input_tokens + written + cached} in "
              f"({cached} cached) / {u.output_tokens} out, ${c:.4f} (total ${self.cost:.4f})",
              file=sys.stderr, flush=True)
        if resp.parsed_output is None:
            raise RuntimeError(f"baseline model gave no parsable answer ({resp.stop_reason})")
        return resp.parsed_output

    def run(self, docs_text: str, as_of) -> tuple[Findings, Findings]:
        first = [{"role": "user", "content": [
            {"type": "text", "text": f"<documents>\n{docs_text}\n</documents>",
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": f"Demo date: {as_of}. Report your findings."}]}]
        before = self._ask(first)
        after = self._ask(first + [{"role": "assistant", "content": before.model_dump_json()},
                                   {"role": "user", "content": moment_message(as_of)}])
        return before, after


def _alert(f: Finding, kinds: dict) -> Alert:
    cites = [Citation(source_id=i, kind=kinds[i]) for i in f.source_ids if i in kinds]
    draft = AlertDraft(id=f"bl-{f.alert}-{f.item_id}", kind=AlertKind(f.alert), manager_id="keyway",
                       invoice_ids=[f.item_id] if f.item_id.startswith("INV-") else [],
                       headline=f"{f.item_id}: {f.alert.replace('_', ' ')}",
                       claims=[Claim(text=f.why, citations=cites)])
    return Alert(draft=draft, verdict=Verdict(alert_id=draft.id, status=VerdictStatus.approved,
                                              reasons=["baseline: no verifier"]))


def to_run(world, rails, before: Findings, after: Findings, cost: float, latency: float) -> RunResult:
    kinds = {d.id: d.kind for d in rails.documents("keyway")}
    by_item = {f.item_id: f for f in after.findings}
    scenarios = []
    for t in world.truth:
        item = t.get("invoice_id") or t.get("manager_id")
        f = by_item.get(item)
        if f is None and not t.get("invoice_id"):          # scenario 4: any pattern finding counts
            f = next((x for x in after.findings if x.alert == "recurring_pattern"), None)
        alerts = [_alert(f, kinds)] if f and f.alert != "none" else []
        cites = [Citation(source_id=i, kind=kinds[i]) for i in (f.source_ids if f else []) if i in kinds]
        scenarios.append(ScenarioResult(
            scenario_id=t["scenario"],
            cause=Cause(f.cause) if f and f.cause != "none" else None,
            cause_citations=cites, alerts=alerts, actions=list(f.actions) if f else [],
            latency_s=latency / len(world.truth)))
    scenarios[0].cost_usd = round(cost, 4)

    unexplained = lambda fs: {f.item_id: f for f in fs.findings if f.alert == "unexplained_unpaid"}
    b, a = unexplained(before), unexplained(after)
    withdrawn = []
    for i in sorted(set(b) - set(a)):
        al = _alert(b[i], kinds)
        reason = by_item[i].why if i in by_item else "no longer reported"
        withdrawn.append(al.model_copy(update={"state": "withdrawn", "withdrawn_reason": reason}))
    changed = [f.item_id for f in after.findings
               if f.model_dump() != ({x.item_id: x for x in before.findings}.get(f.item_id) or f).model_dump()
               or f.item_id not in {x.item_id for x in before.findings}]
    moment = MomentResult(rechecked=changed, withdrawn=withdrawn,
                          kept=[_alert(a[i], kinds) for i in sorted(set(b) & set(a))])
    return RunResult(system="single_model", scenarios=scenarios, moment=moment)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--clean", type=int, default=30)
    p.add_argument("--seed", type=int, default=13)
    p.add_argument("--budget", type=float, default=1.0)
    p.add_argument("--dry-run", action="store_true", help="count tokens and estimate cost only")
    p.add_argument("--out")
    a = p.parse_args(argv)

    world = generate(a.seed, ALL_SCENARIOS, a.clean)
    rails = MemoryRails(world.events())
    docs = [d for d in rails.documents("keyway") if d.dated is None or d.dated <= world.as_of]
    text = _render_evidence(docs)
    if a.dry_run:
        import anthropic
        n = anthropic.Anthropic().messages.count_tokens(
            model=MODEL, system=SYSTEM, messages=[{"role": "user", "content": text}]).input_tokens
        pin, pout, pcache = PRICES.get(MODEL, (4.0, 20.0, 0.20))
        for out in (4000, 12000, 25000):
            est = (n * pin * 1.25 + n * pcache + 2 * out * pout + out * pin) / 1e6
            print(f"{len(docs)} docs, {n} input tokens; if ~{out} output tokens per call: "
                  f"~${est:.2f} for both calls on {MODEL}")
        return 0

    bl = Baseline(model=MODEL, budget_usd=a.budget)
    before, after = bl.run(text, world.as_of)
    run = to_run(world, rails, before, after, bl.cost, bl.latency)
    md = to_markdown([score(run)])
    md = md.replace("# Eval results\n", f"# Eval results: single-model baseline\n\nMode: one {MODEL} "
                    f"prompt with all {len(docs)} documents, plus one follow-up for the moment; "
                    f"{bl.calls} calls, ${bl.cost:.4f}. No workflow, agents or verifier. "
                    "recon_ok and prohibited actions don't apply (it moves no money and keeps no books).\n", 1)
    if a.out:
        with open(a.out, "w") as f:
            f.write(md)
        print(f"wrote {a.out}")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
