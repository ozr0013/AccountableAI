"""Step 2, blind run: python -m workflow.sealed.blind_run <dir>

Runs in its own process with only <dir>/events.jsonl as input. Before any project code runs:
  * an audit hook refuses to open any file listed in the commitment (the answers),
  * a meta-path blocker refuses to import evals/ or world/ (answer keys and the generator's
    planted truth), including via importlib.
Every refused attempt is recorded in outputs.json; a run with any attempt is invalid.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

BLOCKED_MODULES = ("evals", "world")
_attempts: list[str] = []


def _blocked_module(name: str) -> bool:
    return any(name == m or name.startswith(m + ".") for m in BLOCKED_MODULES)


class _ImportBlocker:
    """First finder on sys.meta_path: every import route (statement, importlib) goes through it."""

    def find_spec(self, name, path=None, target=None):
        if _blocked_module(name):
            _attempts.append(f"import {name}")
            raise ImportError(f"sealed eval: importing {name!r} is not allowed during the run")


def install_guard(sealed_files: list[str]) -> None:
    sealed = {os.path.realpath(p) for p in sealed_files}
    leaked = [m for m in sys.modules if _blocked_module(m)]
    if leaked:
        raise SystemExit(f"sealed eval: answer modules already loaded before the run: {leaked}")

    def hook(event, args):
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            p = os.path.realpath(os.fsdecode(args[0]))
            if p in sealed:
                _attempts.append(f"open {p}")
                raise PermissionError(f"sealed eval: {p} is sealed during the run")

    sys.meta_path.insert(0, _ImportBlocker())
    sys.addaudithook(hook)


def _run(out: Path) -> dict:
    # Project imports happen only after the guard is installed.
    from contracts.fakes import FakeVerifier
    from workflow import Deps, Workflow
    from workflow.approvals import RailsApprovals
    from workflow.book import WorkflowBook
    from workflow.memrails import MemoryRails

    from .capture import CapturingAgents, agents, explain_all, statuses
    from .common import INPUTS, read_events

    phases = read_events(out / INPUTS)
    rails = MemoryRails()
    caught = CapturingAgents(agents())
    wf = Workflow(Deps(rails, caught, FakeVerifier(), RailsApprovals(rails)))

    results = wf.replay(phases["replay"])
    open_before = sorted(WorkflowBook.load(rails).open_ids)
    moment = wf.replay(phases.get("moment", []))
    results += moment
    book = WorkflowBook.load(rails)
    status = statuses(results)
    explain_all(rails, caught, status)

    fact = next((e for e in phases.get("moment", []) if e.type == "fact_corrected"), None)
    fact_run = next((r for r in moment if fact and r.event_id == fact.id), None)
    managers = sorted({e.manager_id for e in phases["replay"] if e.manager_id})
    invoice_manager = {l.invoice_id: m for m in managers for l in rails.ledger(m)}
    return {
        "alerts": [a.model_dump(mode="json") for a in book.alerts.values()],
        "open_before_moment": open_before,
        "moment_rechecked": sorted(set(rails.dependents_of(fact.id)) | {fact.invoice_id})
                            if fact else [],
        "moment_withdrawn": fact_run.withdrawn if fact_run else [],
        "chased_invoices": sorted({i for msgs in book.chases.values() for c in msgs
                                   for i in c.invoice_ids}),
        "chase_held": [{"manager_id": e.manager_id, "policy": e.payload.get("policy")}
                       for e in rails.events() if e.type == "chase_held"],
        "bank_change_held": sorted({e.invoice_id for e in rails.events()
                                    if e.type == "bank_change_held" and e.invoice_id}),
        "status": status,
        "findings": {i: f.model_dump(mode="json") for i, f in caught.findings.items()},
        "invoice_manager": invoice_manager,
        "recon": {m: rails.reconcile(m) for m in managers},
    }


def main(argv: list[str] | None = None) -> int:
    out = Path((argv or sys.argv[1:])[0])
    commitment = json.loads((out / "commitment.json").read_text())   # hashes + paths only
    install_guard(list(commitment["sealed_files"]))
    try:
        outputs = _run(out)
        error = None
    except Exception as e:      # noqa: BLE001 - recorded, the reveal step rejects the run
        outputs, error = {}, f"{type(e).__name__}: {e}"
    outputs["blocked_attempts"] = list(_attempts)
    outputs["error"] = error
    data = json.dumps(outputs, indent=1, sort_keys=True, default=str).encode()
    (out / "outputs.json").write_bytes(data)
    (out / "outputs.sha256").write_text(hashlib.sha256(data).hexdigest() + "\n")
    return 1 if (error or _attempts) else 0


if __name__ == "__main__":
    raise SystemExit(main())
