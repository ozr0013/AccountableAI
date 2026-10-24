# Basic autonomous ingestion: incremental delivery

Branch: aaryan/messy-world-audit. Owner: Aaryan.

## Part 1 — integrate, do not replace (implemented and integration-tested)
- Replace the duplicate simulator coordinator with a thin adapter to Workflow.ingest/handle/resume.
- Existing workflow owns assessment, verification, alerts, chasing and case proposals.
- Intake checks source facts; audit observes results and requests selective rechecks.
- Verify unpaid -> manager claim (stays open) -> bank proof (withdrawn), plus replay.

## Part 2 — files a person can actually use (in progress)
- Import PDF text, text/CSV/JSON data, and exported .eml emails with source provenance.
- Persist append-only events between commands and show review items, ledger and open cases.
- Controller explicitly confirms agreement terms; clock is explicit and only moves forward.
- Clearly report scanned/unreadable PDFs and uncertain extraction as review, never empty success.

## Part 3 — prove the basic end-to-end milestone (next)
- Exercise file imports through the existing workflow, including a later corrective document.
- Run tests, the clean 9/9 harness and sealed workflow.
- Provide exact local commands and state remaining limits (OCR, inbox connection, outbound delivery).
- Announce basic end-to-end readiness only after this usable path has been verified.

## Later — evaluation and expanded autonomy
The messy-world generator, audit fixtures and comparison runner remain evaluation additions.
Previous parallel-coordinator benchmarks are superseded and cannot describe the integrated workflow.
Broad paid comparisons and autonomy expansion wait until the basic integration is usable.

Do not edit contracts/ or other owners' folders as part of this branch.
Existing README.md and .gitignore edits predate this integration step and are preserved.

Publication checkpoint: file readers and the local log adapter are implemented, but the
file-ingestion CLI/watch interface and full file-based acceptance tests are not complete.
This branch is work in progress, not a completed basic end-to-end release.
