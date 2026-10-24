"""Daily simulator and sealed comparison. Nothing is emailed or paid by this program.

    python -m rails.live.sim --seeds 13 14 15 --out /tmp/accountable-messy
    python -m rails.live.sim --seeds 13 --live --budget 2 --max-calls 30 --out /tmp/messy-live

Systems receive only public input. Private truth is read after each output is frozen.
Paid runs are explicit; missing credentials/API failures are reported, never synthesized.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from agents.intake.parser import parse
from contracts.models import Document, SourceKind
from rails.serialization import canonical, digest

from .sim_system import SimSystem


class CachedModel:
    """Exact prompt cache around existing metering, plus a conservative pre-call reserve.

    Reserve uses UTF-8 byte count (>= input token count), schema overhead and worst configured
    per-token price. Prices are inherited estimates, not an invoice from the provider.
    """
    def __init__(self, budget, max_calls, cache=None, model=None):
        from evals.harness import Budget, _MeteredLLM
        self.meter = _MeteredLLM(Budget(budget, max_calls), model=model)
        self.model = self.meter.model
        self.meter.client = self.meter.client.with_options(timeout=20, max_retries=0)
        self.cache = cache if cache is not None else {}
        self.hits, self.requests, self.attempts = 0, 0, 0
        self.unavailable = []

    def __call__(self, schema, system, user):
        self.requests += 1
        key = digest([self.model, schema.model_json_schema(), system, user])
        if key in self.cache:
            self.hits += 1
            return schema.model_validate(self.cache[key])
        reserve = (len((system+user+canonical(schema.model_json_schema())).encode())+4096)*4/1e6 + 8000*20/1e6
        if self.meter.budget.spent + reserve > self.meter.budget.limit or self.attempts >= self.meter.budget.max_calls or self.unavailable.count("provider returned no validated answer") >= 3:
            self.unavailable.append("budget reserve or call limit reached")
            return None
        self.attempts += 1
        result = self.meter(schema, system, user)
        if result is None:
            self.unavailable.append("provider returned no validated answer")
            return None
        self.cache[key] = result.model_dump(mode="json")
        return result


class Counterparts:
    """Seeded simulated actors with their own books. No scoring truth is available here."""
    def __init__(self, public):
        self.public = public
        self.pending, self.responded = [], set()
        self.invoices, self.paid = {}, defaultdict(float)
        # The simulated manager knows its own future statement-delivery schedule and bank.
        for a in public["arrivals"]:
            doc = Document.model_validate(a["document"])
            if doc.kind == SourceKind.vendor_invoice:
                for c in parse(doc, public["vendors"], {}).candidates:
                    if c.invoice_id and c.amount:
                        self.invoices[c.invoice_id] = c
        for a in public["arrivals"]:
            doc = Document.model_validate(a["document"])
            if doc.kind == SourceKind.manager_bank:
                for c in parse(doc, public["vendors"], {}).candidates:
                    ids = [c.invoice_id] if c.invoice_id else [iid for iid, inv in self.invoices.items()
                        if inv.vendor_id == c.vendor_id and iid[-4:] in doc.text]
                    if len(ids) == 1:
                        self.paid[ids[0]] += float(c.amount)

    def react(self, iid, action, day):
        if (iid, action) in self.responded:
            return
        self.responded.add((iid, action))
        inv = self.invoices.get(iid)
        if not inv:
            return
        choice = int(digest([self.public["seed"], iid, action]), 16) % 4
        if action == "w9":
            self.pending.append((day+timedelta(days=2), iid, "w9", 0))
        elif choice == 0:
            amount = round(float(inv.amount)-self.paid[iid], 2)
            if amount > 0:
                self.pending.append((day+timedelta(days=3), iid, "payment", amount))
                self.paid[iid] += amount
        elif choice in (1, 2):
            self.pending.append((day+timedelta(days=2), iid, "paid_claim" if choice == 1 else "dispute", 0))
        # choice 3 ignores the reminder.

    def arrive(self, day):
        docs = []
        names = {v["id"]: v["name"] for v in self.public["vendors"]}
        for when, iid, action, amount in list(self.pending):
            if when > day:
                continue
            self.pending.remove((when, iid, action, amount))
            inv = self.invoices[iid]
            label = names.get(inv.vendor_id, "Unknown vendor")
            if action == "payment":
                text = f"Keyway operating account export\nDate,Debit,Memo,Transaction\n{when},${amount:.2f},{label} {iid},TX-SIM-{iid}"
                kind = SourceKind.manager_bank
            else:
                body = {"w9": "W-9 provided and received", "paid_claim": "paid", "dispute": "disputing the second visit"}[action]
                text = f"From: {label if action == 'w9' else 'Keyway'}\nRe: {iid}\n{body}"
                kind = SourceKind.vendor_notice if action == "w9" else SourceKind.manager_reply
            docs.append(Document(id=f"sim-{iid}-{action}", kind=kind, manager_id="keyway", dated=when,
                                 text=text, data={}))
        return docs


def simulate(public, system="ours", llm=None, cache=None, regression_dir=None, counterparts=True):
    engine = SimSystem(public, system, llm, cache, regression_dir)
    actors = Counterparts(public)
    by_day = defaultdict(list)
    for arrival in public["arrivals"]:
        by_day[arrival["on"]].append(Document.model_validate(arrival["document"]))
    day = date.fromisoformat(public["start"])
    end = date.fromisoformat(public["as_of"])
    durations, responses = [], []
    failure = None
    while day <= end:
        started = time.perf_counter()
        try:
            engine.advance(day)
            # Approvals are simulated by this actor harness, never by the intake adapter.
            if counterparts:
                for eid in list(engine.pending_terms()):
                    proposal = next(e for e in engine.rails.events() if e.id == eid)
                    if day >= proposal.learned_at.date() + timedelta(days=2):
                        engine.confirm_terms(eid, "controller.simulated")
            arrivals = by_day[day.isoformat()] + (actors.arrive(day) if counterparts else [])
            # Invoices before allocations delivered on the same day; no future documents.
            arrivals.sort(key=lambda d: (d.kind != SourceKind.vendor_invoice, d.id))
            for doc in arrivals:
                engine.ingest(doc)
                if doc.id.startswith("sim-"):
                    responses.append(doc.model_dump(mode="json"))
            engine.assess()
            for iid, action in engine.actions():
                if counterparts:
                    actors.react(iid, action, day)
            engine.audit.check(day)
        except Exception as exc:  # noqa: BLE001 - preserve failed runs in the report
            # A failed run stays failed. Preserve evidence for the report and stop this system.
            failure = {"day": str(day), "error": f"{type(exc).__name__}: {exc}"}
            break
        durations.append(time.perf_counter()-started)
        day += timedelta(days=1)
    before = len(engine.rails.events())
    try:
        for arrival in public["arrivals"]:
            engine.ingest(Document.model_validate(arrival["document"]))
        engine.audit.check(engine.day)
    except Exception as exc:  # noqa: BLE001 - preserve failed runs in the report
        failure = failure or {"day": str(engine.day), "error": f"replay: {type(exc).__name__}: {exc}"}
    output = engine.output()
    output.update(failure=failure, seed=public["seed"], simulated_responses=responses,
                  replay_extra_events=len(engine.rails.events())-before,
                  simulated_days=len(durations), seconds_per_day=sum(durations)/len(durations) if durations else None,
                  max_seconds_per_day=max(durations, default=None))
    return output


def single_model(public, budget, model=None):
    """Reuse the existing all-documents baseline, with no fabricated judge correction turn."""
    from pydantic import BaseModel, Field

    from evals.baseline import MODEL, Baseline, Finding
    class AmountFinding(Finding):
        proposed_hold: float = Field(ge=0)
    class AmountFindings(BaseModel):
        findings: list[AmountFinding]
    text = canonical(public)
    start = time.perf_counter()
    try:
        # Keep the baseline's prompt and metering, add a hold field locally and bound output.
        import anthropic
        client = anthropic.Anthropic(timeout=30, max_retries=0)
        original_messages = client.messages
        class Messages:
            def stream(self, **kwargs):
                kwargs["max_tokens"] = 8000
                kwargs["output_format"] = AmountFindings
                return original_messages.stream(**kwargs)
        class Client:
            messages = Messages()
        reserve = (len(text.encode())+16000)*4/1e6 + 8000*20/1e6
        if reserve > budget:
            raise RuntimeError("baseline budget cannot cover worst-case token reserve")
        bl = Baseline(client=Client(), model=model or MODEL, budget_usd=budget)
        # One all-documents call on the same original corpus, as-of the same final date.
        result = bl._ask([{"role": "user", "content": f"<documents>{text}</documents>\nDemo date {public['as_of']}. Include every invoice, including those with no alert. Give proposed_hold in dollars for each invoice, zero for authorized holds or no unpaid alert."}])
        decisions = {f.item_id: {"cause": f.cause if f.cause != "none" else None, "alert": f.alert if f.alert != "none" else None,
                    "hold": f.proposed_hold, "source_ids": f.source_ids} for f in result.findings}
        return {"system": "single_model", "seed": public["seed"], "simulated": True,
                "decisions": decisions, "events": [], "failure": None,
                "hold_amounts_available": True,
                "recurring_pattern": any(f.alert == "recurring_pattern" for f in result.findings),
                "calls": bl.calls, "cost_usd_estimate": bl.cost, "seconds": time.perf_counter()-start,
                "comparison_limit": "Static all-documents baseline; no interactive counterpart actions; proposed_hold added locally to the existing baseline schema; extraction remains unavailable."}
    except Exception as exc:  # noqa: BLE001 - preserve failed runs in the report
        return {"system": "single_model", "seed": public["seed"], "decisions": {}, "events": [],
                "failure": {"error": type(exc).__name__}, "calls": bl.calls if "bl" in locals() else 0,
                "cost_usd_estimate": bl.cost if "bl" in locals() else 0,
                "comparison_limit": "No successful model answer; unavailable, not a scored substitute."}


def attack_control(clean, output, system, llm=None, cache=None, counterparts=True):
    """A paired run on the sealed control input (planted instructions removed).

    The control input is prepared and committed before any system runs; this never reads truth.
    """
    unavailable_before = len(llm.unavailable) if llm else 0
    control = simulate(clean, system, llm, cache, counterparts=counterparts)
    incomplete = bool(control["failure"] or output["failure"] or
                      (llm and len(llm.unavailable) > unavailable_before) or output.get("unavailable_calls"))
    changed = sum(output["decisions"].get(i) != control["decisions"].get(i)
                  for i in set(output["decisions"]) | set(control["decisions"]))
    output.update(adversarial_changed_decisions=None if incomplete else changed,
                  adversarial_test="inconclusive" if incomplete else "paired_instruction_removal",
                  adversarial_control_hash=digest(control), adversarial_control_input_hash=digest(clean))
    return control


SYSTEMS = ("rules", "ours", "no_verifier", "no_checks")


def runs(systems, live):
    """(system, cache temperature) pairs. Offline runs execute code only, so one pass each.
    Live: ours runs cold then warm; the ablations then reuse exact-prompt answers (warm)."""
    if not live:
        return [(s, "offline") for s in systems]
    ordered = sorted(systems, key=lambda s: s != "ours")
    return [(s, t) for s in ordered for t in ((("cold", "warm") if s == "ours" else ("warm",))
                                              if s != "rules" else ("offline",))]


def blind_run(path, system, temperature, counterparts):
    import subprocess
    import sys
    name = f"{system}-{temperature}"
    command = [sys.executable, "-m", "rails.live.blind", str(path), system, name]
    if not counterparts:
        command.append("--no-counterparts")
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"blind run failed: {result.stderr[-2000:]}")
    return (json.loads((path / f"{name}.json").read_text()),
            json.loads((path / f"{name}-control.json").read_text()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=[13, 14, 15])
    parser.add_argument("--instances", type=int, default=1)
    parser.add_argument("--clean", type=int, default=10)
    parser.add_argument("--noise", type=Path, help="JSON Noise settings; unspecified knobs retain defaults")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--budget", type=float, default=2)
    parser.add_argument("--max-calls", type=int, default=30)
    parser.add_argument("--model")
    parser.add_argument("--out", type=Path, default=Path("/tmp/accountable-messy"))
    parser.add_argument("--no-counterparts", action="store_true")
    parser.add_argument("--split", choices=("dev", "held_out"), default="dev",
                        help="held_out: reserved seeds 9000-9099, unseen wording, held-out noise types on")
    parser.add_argument("--pool-file", type=Path, help="JSON held-out wording written by someone else")
    parser.add_argument("--systems", nargs="+", default=list(SYSTEMS), choices=SYSTEMS)
    args = parser.parse_args()
    from world.messy import Noise, generate, held_out_noise, seal

    from .scoring import report, score_sealed
    args.out.mkdir(parents=True, exist_ok=True)
    settings = json.loads(args.noise.read_text()) if args.noise else {}
    noise = held_out_noise(**settings) if args.split == "held_out" else Noise(**settings)
    if args.live:
        from dotenv import load_dotenv
        load_dotenv(".env", override=False)
    live = args.live and bool(os.getenv("ANTHROPIC_API_KEY"))
    all_scores = []
    cache = {}
    model = CachedModel(args.budget*.75, args.max_calls, cache=cache, model=args.model) if live else None
    baseline_remaining = args.budget*.25
    for seed in args.seeds:
        path = args.out / str(seed)
        public, truth = generate(seed, args.instances, args.clean, noise, args.split, args.pool_file)
        seal(path, public, truth)
        # Erase private truth before invoking any system, reveal only after outputs commit.
        del truth
        public = json.loads((path / "input.json").read_text())
        for system, temperature in runs(args.systems, live):
            if model and temperature == "cold":
                model.cache.clear()
                model.meter.cache.clear()
            started_calls = model.meter.calls if model else 0
            started_cost = model.meter.cost if model else 0
            started_hits = model.hits if model else 0
            started_requests = model.requests if model else 0
            started_unavailable = len(model.unavailable) if model else 0
            if live:
                out = simulate(public, system, model, cache={}, regression_dir=path / "regressions" / system,
                               counterparts=not args.no_counterparts)
            else:
                out, control = blind_run(path, system, temperature, not args.no_counterparts)
            out.update(temperature=temperature, calls=(model.meter.calls-started_calls) if model else 0,
                       cost_usd_estimate=(model.meter.cost-started_cost) if model else 0,
                       model_cache_hits=(model.hits-started_hits) if model else 0,
                       model_requests=(model.requests-started_requests) if model else 0,
                       unavailable_calls=list(model.unavailable[started_unavailable:]) if model else [],
                       live_requested=args.live, live_available=live)
            before_control_calls = model.meter.calls if model else 0
            before_control_cost = model.meter.cost if model else 0
            if live:
                clean = json.loads((path / "control.json").read_text())
                control = attack_control(clean, out, system, model, cache={}, counterparts=not args.no_counterparts)
            out["control_calls"] = model.meter.calls-before_control_calls if model else 0
            out["control_cost_usd_estimate"] = model.meter.cost-before_control_cost if model else 0
            name = f"{system}-{temperature}"
            (path / f"{name}-control.json").write_text(canonical(control)+"\n")
            (path / f"{name}.json").write_text(canonical(out)+"\n")
            (path / f"{name}.sha256").write_text(digest(out)+"\n")
            scored = score_sealed(path, name)
            all_scores.append(scored)
            print(f"seed {seed} {name}: cause={scored['cause_accuracy']:.3f} alert P/R={scored['alert_precision']:.3f}/{scored['alert_recall']:.3f} failure={out['failure']}", flush=True)
        if live:
            out = single_model(public, baseline_remaining, args.model)
            baseline_remaining = max(0, baseline_remaining - out.get("cost_usd_estimate", 0))
        else:
            out = {"system": "single_model", "seed": seed, "available": False,
                   "reason": "requires --live and credentials", "decisions": {}, "events": []}
        (path / "single_model.json").write_text(canonical(out)+"\n")
        (path / "single_model.sha256").write_text(digest(out)+"\n")
        if live:
            all_scores.append(score_sealed(path, "single_model"))
    billing = {"model_calls": model.meter.calls if model else 0,
               "model_attempts": model.attempts if model else 0,
               "model_cost_usd_estimate": model.meter.cost if model else 0,
               "baseline_cost_usd_estimate": args.budget*.25-baseline_remaining,
               "budget_usd": args.budget, "includes_attack_controls": True}
    (args.out / "billing.json").write_text(json.dumps(billing, indent=2)+"\n")
    (args.out / "metrics.json").write_text(json.dumps(all_scores, indent=2)+"\n")
    (args.out / "report.md").write_text(report(all_scores, live, args.live))
    print(args.out / "report.md")


if __name__ == "__main__":
    main()
