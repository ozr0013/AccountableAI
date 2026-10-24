"""Step 1, seal: build the inputs and commit to the answers by hash, before anything runs."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from contracts.models import Event

from .common import ANSWER_FILES, COMMITMENT, INPUTS, TRUTH, sha256_file, write_events


def _scan_events(manager_ids: list[str], as_of) -> list[Event]:
    when = datetime.combine(as_of, dtime(23, 0), tzinfo=UTC)
    return [Event(id=f"ev-scan-{m}-{as_of.isoformat()}", type="scan_requested", manager_id=m,
                  valid_from=when, learned_at=when, payload={"as_of": as_of.isoformat()})
            for m in manager_ids]


def _moment_events(as_of, moment: dict) -> list[Event]:
    """What the Judge Console sends during the demo: a bare 'we paid the electrician' reply
    (must change nothing), then the corrected receipt date. Same shape as evals/harness.py."""
    now = datetime.combine(as_of, dtime(12), tzinfo=UTC)
    inv = moment["fact_key"].split(":")[1].split(".")[0]
    reply = Event(id="ev-judge-moment-reply", type="chase_reply", manager_id="keyway",
                  invoice_id=inv, valid_from=now, learned_at=now,
                  payload={"id": "ev-judge-moment-reply", "manager_id": "keyway",
                           "invoice_ids": [inv], "text": "We paid the electrician.",
                           "received_at": now.isoformat(), "claims_paid": True, "proof": []})
    fact = Event(id="ev-judge-moment-fact", type="fact_corrected", manager_id="keyway",
                 invoice_id=inv, valid_from=datetime.fromisoformat(moment["value"]).replace(tzinfo=UTC),
                 learned_at=now + timedelta(minutes=1),
                 payload={"fact_key": moment["fact_key"], "value": moment["value"]})
    return [reply, fact]


def seal(out: Path, seed: int = 13, n_clean: int = 30) -> dict:
    from world import generate
    from world.generator import ALL_SCENARIOS, MOMENT

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    world = generate(seed, ALL_SCENARIOS, n_clean)

    write_events(out / INPUTS, [
        ("replay", world.events() + _scan_events([m.id for m in world.managers], world.as_of)),
        ("moment", _moment_events(world.as_of, MOMENT)),
    ])
    (out / TRUTH).write_text(json.dumps(world.truth, indent=1, sort_keys=True, default=str))

    sealed = {str(p): sha256_file(p) for p in [*ANSWER_FILES, out / TRUTH]}
    commitment = {
        "sealed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "seed": seed, "n_clean": n_clean,
        "sealed_files": sealed,
        "inputs_sha256": sha256_file(out / INPUTS),
    }
    commitment["commitment_sha256"] = hashlib.sha256(
        json.dumps(commitment, sort_keys=True).encode()).hexdigest()
    (out / COMMITMENT).write_text(json.dumps(commitment, indent=1, sort_keys=True))
    return commitment
