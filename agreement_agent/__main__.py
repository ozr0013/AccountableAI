"""Run the agreement agent.

    python -m agreement_agent --local agreement_agent/samples          # a folder (rules mode, free)
    python -m agreement_agent --gmail                                  # today's mail only (read-only)
    python -m agreement_agent --gmail --sent-on 2026-10-24             # mail that arrived on that day
    python -m agreement_agent --gmail --query 'from:vendor.com has:attachment'
    python -m agreement_agent --local FILE.pdf --model                 # Claude picks the tool calls (paid)
"""

from __future__ import annotations

import argparse
import logging
from datetime import date

from .connectors import DEFAULT_QUERY, Gmail, LocalFolder
from .graph import review
from .store import save

MARK = {"found": "ok ", "missing": "-- ", "unclear": "?? "}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Agreement agent: read documents, check the payment terms, escalate gaps.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--local", metavar="PATH", help="a folder or a single file")
    src.add_argument("--gmail", action="store_true", help="read attachments from Gmail (read-only)")
    ap.add_argument("--query", default=DEFAULT_QUERY, help="Gmail search query")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--sent-on", type=date.fromisoformat, default=date.today(), metavar="YYYY-MM-DD",
                    help="Gmail: only mail that arrived on this day (default today)")
    ap.add_argument("--model", action="store_true", help="let Claude drive the tools (costs money)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every step the agent took")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("pypdf").setLevel(logging.ERROR)

    docs = Gmail().fetch(a.query, a.limit, sent_on=a.sent_on) if a.gmail else LocalFolder(a.local).fetch(a.limit)
    n = escalated = skipped = 0
    total = 0.0
    for doc in docs:
        r = review(doc, use_model=a.model)
        n += 1
        total += r.cost_usd
        if not r.relevant:
            skipped += 1
            print(f"\n{r.filename}  skipped: not an agreement or invoice" + (f"  from {r.sender}" if r.sender else ""))
            continue                       # skipped documents are never written to disk
        save(r)
        print(f"\n{r.filename}  [{r.kind}]  {'complete' if r.complete else 'NEEDS THE OWNER'}"
              + (f"  from {r.sender}" if r.sender else "") + (f"  ${r.cost_usd:.4f}" if r.mode == "model" else ""))
        for f in r.fields:
            print(f"  {MARK[f.status]}{f.label:28} {f.value or f.note}" + (f"  ({f.location})" if f.location else ""))
        if a.verbose:
            for s in r.steps:
                print(f"     · {s}")
        if r.escalation:
            escalated += 1
            print(f"  -> escalated to the owner [{r.escalation.severity}]: {r.escalation.subject}")
    print(f"\n{n} document(s): {n - skipped} checked, {escalated} escalated, {skipped} skipped" + (f", ${total:.4f} model spend" if a.model else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
