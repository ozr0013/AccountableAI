# Autonomous vendor payment monitoring

Built from `kani/workflow` (`a44def1`) for the owner → management → vendor workflow.

The existing workflow extracts agreement terms for human confirmation, computes invoice
deadlines, chases unpaid invoices using approved templates and schedules, and withdraws
unsupported alerts when evidence changes. This change adds a database worker and a review
after bank payments. Existing live agent reasoning continues to stream from the workflow.

## Run

Use the project Python environment with dependencies installed, and an already loaded
PostgreSQL database (`DATABASE_URL`). Run from the repository root. Do not reset an existing
database to try this feature.

The monitor loads `.env` from the current directory. Exported environment variables take
precedence. Following live verification, the local worktree uses the original project's
`.env` through a symlink, with `RAILS=real`, `AGENTS=real`, `AGENTS_LLM=on`, `VERIFIER=real`,
and the local PostgreSQL connection. This uses real Claude API calls when new work is due.
For a one-time run using that configuration:

```sh
/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/.venv/bin/python -m workflow.monitor --clock world --once
```

Local connection verification uses PostgreSQL 17 on `localhost:5432`, database `accountable`.
The empty application database was seeded with generated demo data; existing databases were
not reset. Integration tests use separate temporary schemas and clean them up afterwards.
The first CLI sweep processed 344 inputs/scans and persisted 103 payment reviews and queued
updates. Repeating the same sweep left the event count unchanged at 1,400. PostgreSQL remains
running; the continuous monitor was not started. The database was started with `pg_ctl`, so
automatic startup at login is not configured.

```sh
# One sweep using the seeded world's date, without advancing the demo.
RAILS=real AGENTS=real VERIFIER=real python -m workflow.monitor --clock world --once

# Continuous operation using UTC wall time: poll every 30 seconds, scan once each day.
RAILS=real AGENTS=real VERIFIER=real python -m workflow.monitor --clock wall --interval 30
```

`VERIFIER=real` needs Anthropic credentials. `AGENTS=real` uses Claude when configured and
otherwise falls back to deterministic agents. For offline tests, use `RealAgents(None)` and
the explicitly simulated verifier, as in `workflow/tests/test_monitor.py`.

Run **one workflow writer** per database. This worker polls for documents and corrections
already ingested into the event log; payment review begins on the next poll. It is a separate
process, not automatically launched by the dashboard. The default clock is `world` to protect
the demo from jumping from April to the actual date. Use `wall` deliberately for real operation.

## Payment review

Each bank payment is compared with the invoice, its expected vendor, and the agreement's
deadline. Checks include underpayment, overpayment, wrong vendor, missing recipient/date,
late payment, and missing or inconsistent management statement. A confirmed agreement's
inspection holdback reduces the amount currently payable. An explicit mismatching vendor
never counts as payment proof; legacy records without a vendor ID remain readable and get
a `recipient_unknown` review finding. Unique vendor/amount/date matches without invoice
references use the same resolved associations as the ledger.

Supporting statements use the existing `manager_statement` document shape: document date
plus `data.lines` containing `invoice_id`, `amount`, `status`, and optionally `on`. A statement
must cover the payment date. This is structured statement reconciliation; arbitrary financial
statement/PDF extraction is not part of this change.

The append-only database records are:

| Event | Meaning |
| --- | --- |
| `payment_reviewed` | Amounts, deadline, findings, source IDs and previous review ID |
| `finance_update_queued` | Internal update addressed to management and owner |
| `statement_request_drafted` | Request for supporting statement; exact draft requires approval |

These updates and requests are internal records, not delivered email. There is no email
connector, approval UI for these new drafts, or money-moving action in this change. Existing
unpaid alerts still use the approved polite → firm → escalation ladder; daily monitoring
continues after escalation without bypassing its send policy.

Unchanged evidence produces no new review. A statement, corrected bank record or daily scan
can produce a new revision. Removed payment evidence explicitly revises the prior result.
The worker resumes unfinished inputs from the log; review outputs have deterministic IDs,
and retries repair partial writes without changing the original review's content. New facts
arriving during downtime are handled by a subsequent event or scan.

## Proof

Real Claude and background polling were also verified; see
[the live verification report](CLAUDE_POSTGRES_VERIFICATION.md) and
[machine-readable evidence](CLAUDE_POSTGRES_EVIDENCE.json).

With local PostgreSQL enabled, the full unfiltered suite passed: **385 passed, 2 skipped**
(2026-10-24). The PostgreSQL monitor integration and environment-loading regression are included.

```sh
python -m pytest workflow/tests/test_monitor.py rails/tests/test_matcher.py workflow/tests/test_chase_flow.py -q
```

Tests exercise daily scan deduplication and restart, new payments arriving after the daily
scan, incorrect recipients and amounts, late and on-time payments, authorized holdbacks,
statement arrival/mismatch, missing invoice references, removed bank evidence, and crashes
between the review and downstream updates (including new evidence arriving during downtime).
These tests use the real workflow and matching code with an in-memory event store and stand-in
verifier. They do not establish PostgreSQL availability or external message delivery.
