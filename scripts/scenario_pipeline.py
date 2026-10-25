"""Live end-to-end run of the agent pipeline for one invoice, with real Claude calls.

    RAILS=real python scripts/scenario_pipeline.py      # INV-KW-0301, about $0.20-0.40

agreement (already saved) -> chaser agent (Apr 22, Apr 27) -> bank payment -> chaser agent
(Apr 30) hands off -> anomaly agent verdict. Runs on a throwaway copy of the Postgres event log;
the queue and chaser rows written to SpacetimeDB are cleared at the end.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=False)
os.environ["SPACETIME"] = "off"
INV = "INV-KW-0301"
BAR = "-" * 96


def show(title, value):
    print(f"\n{BAR}\n{title}\n{BAR}")
    print(value if isinstance(value, str) else json.dumps(value, indent=2, default=str))


def summarize(task):
    t = task["trace"]
    return {"task": task["task_id"], "created_by": task["created_by"], "status": task["status"],
            "reasoning": t.get("thinking", []),
            "tool_calls": [{"tool": s["tool"], "input": s["input"], "refused": s["refused"]}
                           for s in t.get("steps", [])],
            "decision": task["outcome"], "usage": t.get("usage"), "model": t.get("model")}


def main():
    from fastapi import HTTPException

    from chaser_agent import api as service
    from chaser_agent import connected, pipeline
    from contracts.models import Document
    from rails.db import connect
    from rails.events.ingest import document_event
    from rails.live.spacetime import SpacetimeMirror
    from rails.real import RealRails

    def locked(fn):
        for _ in range(40):
            try:
                with service._lock, service.workflow_lock(rails):
                    return fn()
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                time.sleep(0.5)
        raise RuntimeError("workflow lock busy")

    mirror = SpacetimeMirror()
    scratch = f"pipeline_{uuid.uuid4().hex[:8]}"
    conn = connect(scratch)
    try:
        rails = RealRails(scratch)
        for e in RealRails().store.events():
            rails.append_event(e)
        mirror.call("clear_tasks", [])
        mirror.call("clear_chaser", [])

        def day(d, label):
            pipeline.enqueue(mirror, "chaser", INV, f"daily check ({label})", {}, d, "daily_schedule")
            locked(lambda: pipeline.process(rails, mirror, d, invoice_id=INV, limit=4))
            for t in pipeline.tasks(mirror, invoice_id=INV):
                if t["as_of"] == d.isoformat():
                    show(f"{d} - {t['agent'].upper()} AGENT", summarize(t))

        day(date(2026, 4, 22), "deadline passed")
        polite = next(o for c in connected.cases(rails) if c["invoice_id"] == INV
                      for o in c["outbox"] if o["kind"] == "chase_reminder" and o["status"] == "queued")
        locked(lambda: connected.acknowledge(rails, mirror, polite["id"], "smtp-0422", "controller"))
        show("2026-04-22 - OUTBOX", "polite reminder delivered (receipt smtp-0422)")
        day(date(2026, 4, 27), "follow-up")
        rails.append_event(document_event(Document(
            id="pipeline-bank-0301", kind="manager_bank", manager_id="keyway", dated=date(2026, 4, 29),
            data={"payments": [{"invoice_id": INV, "vendor_id": "v-riogrande",
                                "payee": "Rio Grande Plumbing LLC", "bank": "First Mesa Bank",
                                "routing": "102000021", "account": "4471029", "amount": 1240.0,
                                "on": "2026-04-29"}]}), datetime(2026, 4, 29, 18, tzinfo=UTC)))
        show("2026-04-29 - NEW EVIDENCE", "bank: $1,240 to Rio Grande Plumbing LLC, First Mesa Bank")
        day(date(2026, 4, 30), "payment seen")
        f = pipeline.flow(rails, mirror, INV)
        show("FLOW: the chain for this invoice", {
            "agreement_rules": [r["check"] for r in f["rules"]],
            "tasks": [(t["task_id"], t["created_by"], t["status"], t["outcome"].get("decision"),
                       t["outcome"].get("verdict")) for t in f["tasks"]],
            "chaser_actions": [(h["as_of"], h["action"], h.get("level"), h["status"])
                               for h in f["chaser_actions"]],
            "case": {k: (f["case"] or {}).get(k) for k in ("state", "findings")}})
        total = {"input_tokens": 0, "output_tokens": 0}
        for t in f["tasks"]:
            for k in total:
                total[k] += (t["trace"].get("usage") or {}).get(k, 0)
        show("TOTAL TOKENS", total)
    finally:
        conn.execute(f'DROP SCHEMA "{scratch}" CASCADE')
        conn.close()
        mirror.call("clear_tasks", [])
        mirror.call("clear_chaser", [])
        print(f"\n{BAR}\nCleaned up: scratch schema dropped, queue and chaser rows cleared.\n{BAR}")


if __name__ == "__main__":
    main()
