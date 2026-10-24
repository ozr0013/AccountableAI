# Live Claude + PostgreSQL verification

Verified on 2026-10-24 using `claude-sonnet-5-5`, actual Anthropic calls and local PostgreSQL.
All data sent to Claude was generated test data. The tests used isolated schemas, removed
afterwards, and did not send email or move money.

## Results

The final evidence run passed 14 assertions across five trigger phases, making 16 successful
Claude calls with 16 accepted decisions and zero rules fallbacks. A separate real CLI worker
test made seven additional successful Claude calls, for 23 successful calls in the final checks.

| Trigger | Observed result |
| --- | --- |
| Daily scan | Read the funded $1,240 invoice, calculated the March 13 deadline, raised a verified overdue alert and recorded a reminder |
| Management says “paid” | Claude read the reply; without bank/vendor proof the $1,240 alert stayed open |
| Bank names wrong vendor | Detected recipient mismatch; expected vendor still owed $1,240 |
| Corrected bank proof | Ledger updated to $1,240 paid; stopped unpaid chasing; retained late-payment and missing-statement findings |
| Supporting statement arrives | Removed the missing-statement finding while preserving the historical late-payment finding |
| Repeated poll | No additional Claude calls or duplicate database records |

The actual `python -m workflow.monitor --clock world --interval 1` command was also tested in
a subprocess. It generated the daily scan automatically, then consumed a new reply inserted
directly into PostgreSQL while running. PostgreSQL recorded live decisions for `chaser`,
`trail`, `cause` and `verifier`, including model IDs and token usage. Subsequent polls wrote
no duplicates. This temporary worker was stopped after verification. Normal interval remains
30 seconds; the continuous worker is not currently running.

## Data selection and architecture

Python's `RealRails` layer queries PostgreSQL. The deterministic workflow chooses which
invoice to assess and supplies those retrieved records to Claude. Claude does **not** have
a SQL tool or choose queries itself.

The test recorded returned database document IDs, evidence IDs passed to model calls,
model responses, citations, trigger IDs and token usage. Every cited source was checked
against historical records for the correct manager and the evidence supplied to that agent.
A different manager's invoice, identifier and marker never appeared in Claude's inputs.
The verifier's actual evidence inputs were audited too.

Agreement confirmation and hold proposals were outside this live test; they are covered
by the existing suite. Reminder drafting and post-payment anomaly checks are deterministic
code, so their operation does not imply an additional Claude call.

## Bugs found and repaired

The first live pass exposed two existing defects:

- Streaming responses can contain JSON strings mixed with content blocks. The reader had
  discarded those strings and treated valid Claude answers as empty.
- The cause guard rejected an explanation of a proven late payment despite allowing late
  payments conceptually. It now distinguishes an on-time payment from a proven late one.

Both fixes have regression tests that failed before the fix and passed afterwards. The
corrected live run had zero fallback decisions.

The full regression suite after these fixes passed: **387 passed, 2 skipped**, with no failures.

## Evidence

[CLAUDE_POSTGRES_EVIDENCE.json](CLAUDE_POSTGRES_EVIDENCE.json) contains the final run's
14 assertion results, five trigger phases, database reads, API usage receipts, accepted
decisions, citations and the background-worker result. Local diagnostic scripts are saved
under `.ai/live_claude_probe.py` and `.ai/live_claude_worker_probe.py`.

The normal monitor configuration now selects real PostgreSQL, Claude agents and the Claude
verifier. The worktree `.env` points to the original `.env`; the API key was not duplicated.
