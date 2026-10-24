"""Replay exported audit fixtures without models or a database.

    python -m rails.audit.regression PATH
"""
import argparse
import json
from pathlib import Path

from contracts.models import Event
from workflow.memrails import MemoryRails

from .engine import Audit


def replay(path):
    record = json.loads(Path(path).read_text())
    miss = Event.model_validate(record["mistake"])
    rails = MemoryRails([Event.model_validate(e) for e in record["events"]])
    audit = Audit(rails)
    evidence = miss.payload["evidence"]
    signal = miss.payload["signal"]
    if signal == "outcome":
        # Re-run settlement, not just the expected output constructor. The export was
        # captured after settlement, so remove that derived record before replaying.
        pid = evidence["prediction_id"]
        rails = MemoryRails([e for e in rails.events() if not (e.type == "prediction_settled" and e.payload["prediction_id"] == pid)])
        audit = Audit(rails)
        from datetime import date
        audit.settle(miss.invoice_id, {evidence["target"]: evidence["observed"]},
                     date.fromisoformat(evidence["effective_on"]), miss.learned_at.date(), miss.source,
                     caused_by=evidence["caused_by"])
    elif signal == "invariant":
        from .engine import InvariantViolation
        try:
            audit.check(miss.learned_at.date())
        except InvariantViolation:
            pass
    elif signal == "disagreement":
        audit.disagreement(miss.invoice_id, evidence["rules"], evidence["model"],
                           miss.learned_at.date(), miss.source, evidence["target"])
    else:
        audit.mistake(signal, miss.invoice_id, miss.learned_at.date(), miss.source,
                      evidence, record["expected"]["value"])
    out = next(e for e in rails.events() if e.id == miss.id)
    corrections = [e for e in rails.events() if e.type == "correction_recorded" and e.payload["mistake_id"] == out.id]
    if record["expected"]["value"] is not None:
        assert corrections and corrections[-1].payload["value"] == record["expected"]["value"]
        assert corrections[-1].payload["affected"] == record["expected"]["affected"]
    assert out.id == miss.id
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    paths = list(args.path.glob("*.json")) if args.path.is_dir() else [args.path]
    if not paths:
        raise SystemExit("no regression cases")
    for path in paths:
        replay(path)
    print(f"{len(paths)} permanent regression cases passed")
