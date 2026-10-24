"""Rehearse the live loop against the real database.

    python -m rails.load --fresh                  # the demo world (or --clean 30 for a quicker one)
    python -m rails.live warm                     # process what is already there (once, before the demo)
    python -m rails.live script                   # run the demo script and time every step
    uvicorn rails.console.api:app --port 8001     # then serve it (ONE worker)

Careful: `script` plays the whole demo, so it USES UP the demo state (the moment is applied and
the clock ends at Apr 27; time only moves forward). Before a real demo run
`python -m rails.load --fresh` and then `python -m rails.live warm`, never `script`. Do not run
`warm` or `script` while the API server is up: that would be two writers.

Agents and verifier follow AGENTS / VERIFIER (real = models, with ANTHROPIC_API_KEY). Use
LANGGRAPH_STRICT_MSGPACK=false to silence LangGraph's type warnings.
"""

from __future__ import annotations

import sys
import time

from .system import LiveSystem


def _step(name, fn):
    t = time.monotonic()
    out = fn()
    print(f"  [{time.monotonic() - t:6.1f}s] {name}")
    return out


def _unexplained(live) -> dict[str, float]:
    return {a.draft.invoice_ids[0]: a.draft.amount for a in live._Book.load(live.rails).open_alerts()
            if a.draft.kind.value == "unexplained_unpaid" and a.draft.amount > 0}


def script(live: LiveSystem) -> int:
    print(f"mode: {live.mode()}")
    print(f"start: today {live.rails.now()}, unexplained {_unexplained(live)}")
    _step("funding review ($18,400)", lambda: live.request_funding("keyway", 18400.0))
    hold = next(p for p in live.pending_approvals() if p["funding_amount"] == 18400.0)
    print(f"         -> {hold['recommendation']}")
    _step("controller approves (the one human touch)",
          lambda: live.decide(hold["approval_id"], "controller.dana", True))
    _step("judge: 'Keyway says they paid the electrician'",
          lambda: live.inject_text("Keyway says they paid the electrician", manager_id="keyway"))
    print(f"         -> unexplained {_unexplained(live)}")
    rec, _ = _step("judge fact: invoice received Apr 15",
                   lambda: live.inject_preset("invoice_received_correction"))
    print(f"         -> {rec.summary}\n         -> rechecked {rec.affected}; unexplained {_unexplained(live)}")
    _step("judge fact: bank shows the plumber paid",
          lambda: live.inject_preset("bank_shows_payment", {"invoice_id": "INV-KW-0301", "on": "2026-04-22"}))
    print(f"         -> unexplained {_unexplained(live)}")
    for _ in range(3):
        _, _, target, reason = _step("advance time", live.advance_clock)
        print(f"         -> {target}: {reason}; unexplained {_unexplained(live)}")
    s = live.stats()
    print(f"human decisions {s['human_decisions']} | automatic actions {s['automatic_actions']} "
          f"| pending approvals {s['pending_approvals']}")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "script"
    live = LiveSystem()
    if cmd == "warm":
        print(_step("warm start", live.warm))
        print(live.stats())
        return 0
    if cmd == "script":
        return script(live)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
