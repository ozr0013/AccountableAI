"""Walk one invoice through the chaser, printing every input, decision, Claude call and hand-off.

    RAILS=real python scripts/scenario_chaser.py            # INV-KW-0301, Rio Grande Plumbing

Runs on a throwaway copy of the Postgres event log, so injecting the scenario's bank payment
never touches the real data. Chaser actions are written to SpacetimeDB during the run and
cleared at the end, then today's real state is restored.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=False)
os.environ["SPACETIME"] = "off"         # the scratch copy must not be mirrored

INV = "INV-KW-0301"
BAR = "=" * 96


def show(title, value=None):
    print(f"\n{BAR}\n{title}\n{BAR}")
    if value is not None:
        print(json.dumps(value, indent=2, default=str) if not isinstance(value, str) else value)


def with_retry(fn, tries=20):
    """The workflow lock is database-wide; a running worker may hold it for a moment."""
    import time

    from fastapi import HTTPException
    for attempt in range(tries):
        try:
            return fn()
        except HTTPException as exc:
            if exc.status_code != 409 or attempt == tries - 1:
                raise
            time.sleep(0.5)


def main() -> None:
    from agents.chaser import ChaserAgent
    from agents.llm import capture_attempts, default_llm
    from chaser_agent import api as chaser_api
    from chaser_agent import daily
    from chaser_agent.assistant import SYSTEM, TOOLS, DataTools, ask
    from contracts.models import Document
    from rails.db import connect
    from rails.events.ingest import document_event
    from rails.live.spacetime import SpacetimeMirror
    from rails.real import RealRails

    real, mirror = RealRails(), SpacetimeMirror()
    scratch = f"scenario_{uuid.uuid4().hex[:8]}"
    conn = connect(scratch)
    try:
        rails = RealRails(scratch)
        for e in real.store.events():
            rails.append_event(e)
        mirror.call("clear_chaser", [])

        line = next(l for l in rails.ledger("keyway") if l.invoice_id == INV)
        rules = [r for r in mirror.query("SELECT * FROM agreement_rule")
                 if daily.norm_name(r["vendor_name"]) == daily.norm_name("Rio Grande Plumbing")]
        show("STEP 1 - INPUT TO THE CHASER: the invoice (Postgres ledger)", line.model_dump(mode="json"))
        show("STEP 1 - INPUT TO THE CHASER: Rio Grande's agreement rules (SpacetimeDB, from the "
             "agreement agent)", [{k: r[k] for k in ("rule_id", "rule_type", "status", "check",
                                                    "source_quote")} for r in rules])
        show("SKILL FILES USED", "None. The chaser has no SKILL.md: the daily check is code, and "
             "Claude is guided only by its system prompt and the tool descriptions below.")

        def run_day(day):
            out = with_retry(lambda: chaser_api.run_once(day, rails=rails, mirror=mirror))
            return next(d for d in out["decisions"] if d["invoice_id"] == INV)

        show("STEP 2 - DAILY CHECK, 2026-03-30 (code only, no Claude)", run_day(date(2026, 3, 30)))
        show("STEP 3 - DAILY CHECK, 2026-04-22 (deadline passed)", run_day(date(2026, 4, 22)))
        from chaser_agent import connected
        polite = next(o for c in connected.cases(rails) if c["invoice_id"] == INV
                      for o in c["outbox"] if o["kind"] == "chase_reminder" and o["status"] == "queued")
        delivered = connected.acknowledge(rails, mirror, polite["id"], "smtp-receipt-0422",
                                          "controller.dana")
        show("STEP 3b - OUTBOX: polite reminder confirmed delivered (receipt recorded)",
             {k: delivered[k] for k in ("id", "kind", "status", "reference", "acknowledged_by")})

        reply = "Hi, we paid Rio Grande for this one last week, it should have cleared by now."
        docs = rails.documents("keyway", INV)
        with capture_attempts() as calls:
            reading = ChaserAgent(default_llm()).read_reply(reply, "keyway", docs, [INV])
        show("STEP 4 - KEYWAY REPLIES; CLAUDE READS IT (input)",
             {"reply": reply, "records_shown_to_claude": [f"{d.id} ({d.kind.value})" for d in docs]})
        show("STEP 4 - CLAUDE CALL + CODE GUARD (thinking, answer, accepted?)",
             [{k: c.get(k) for k in ("model", "attempt", "accepted", "problems", "answer",
                                     "thinking", "input_tokens", "output_tokens")} for c in calls])
        show("STEP 4 - OUTPUT", {"claims_paid": reading.claims_paid,
                                 "proof": [c.source_id for c in reading.proof],
                                 "verdict": "holding position: no bank record shows the payment"
                                 if reading.claims_paid and not reading.proof else "see proof"})

        show("STEP 5 - DAILY CHECK, 2026-04-27 (reply was not proof; ladder continues)",
             run_day(date(2026, 4, 27)))

        question = f"What is going on with {INV} and what should the controller do?"
        tools = DataTools(rails, mirror, lambda: chaser_api.history(mirror), date(2026, 4, 27))
        show("STEP 6 - ASK THE CHASER (Claude + read-only tools): system prompt", SYSTEM)
        show("STEP 6 - tools Claude can call", [{"name": t["name"], "description": t["description"]}
                                                for t in TOOLS])
        result = ask(question, tools)
        show("STEP 6 - Claude's reasoning summary (thinking)", result["thinking"])
        show("STEP 6 - tool calls Claude chose, in order",
             [{"tool": t["tool"], "input": t["input"], "result": t["result_preview"][:220]}
              for t in result["tools"]])
        show("STEP 6 - Claude's answer", result["answer"] or result.get("error"))
        show("STEP 6 - usage", {**result["usage"], "model": result["model"]})

        bank = Document(id="scenario-bank-0301", kind="manager_bank", manager_id="keyway",
                        dated=date(2026, 4, 29), text="Keyway operating account, outgoing ACH",
                        data={"payments": [{"invoice_id": INV, "vendor_id": "v-riogrande",
                                            "payee": "Rio Grande Plumbing LLC",
                                            "bank": "First Mesa Bank", "routing": "102000021",
                                            "account": "4471029", "amount": 1240.0,
                                            "on": "2026-04-29"}]})
        rails.append_event(document_event(bank, datetime(2026, 4, 29, 18, tzinfo=UTC)))
        show("STEP 7 - NEW EVIDENCE: bank record (scratch copy only)", bank.model_dump(mode="json"))
        handoff = run_day(date(2026, 4, 30))
        show("STEP 8 - DAILY CHECK, 2026-04-30 -> HAND-OFF TO THE ANOMALY AGENT", handoff)
        record = next(h for h in chaser_api.history(mirror)
                      if h["invoice_id"] == INV and h["action"] == "handoff_anomaly")
        show("STEP 8 - hand-off record the anomaly agent picks up (SpacetimeDB chase_action)",
             record)
        case = next(c for c in connected.cases(rails) if c["invoice_id"] == INV)
        show("STEP 9 - ANOMALY CHECKS: the case verdict (Postgres workflow_case)",
             {k: case[k] for k in ("state", "findings", "closure_eligible", "source_ids")})
        show("STEP 9 - outbox for this invoice after the hand-off (stale drafts cancelled)",
             [{k: o[k] for k in ("kind", "status", "audience", "body")} for o in case["outbox"]])
        show("TRAIL FOR THIS INVOICE (every recorded chaser action)",
             [{k: h.get(k) for k in ("as_of", "action", "level", "status", "decided_by",
                                     "decision_note", "reason")}
              for h in chaser_api.history(mirror) if h["invoice_id"] == INV])
    finally:
        conn.execute(f'DROP SCHEMA "{scratch}" CASCADE')
        conn.close()
        mirror.call("clear_chaser", [])
        with_retry(lambda: chaser_api.run_once(None, rails=RealRails(), mirror=mirror))
        print(f"\n{BAR}\nCleaned up: scratch schema dropped, chaser state restored to today.\n{BAR}")


if __name__ == "__main__":
    main()
