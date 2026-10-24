# One invoice: live end-to-end agent trace

**Vendor:** Rio Grande Plumbing · **Invoice:** INV-KW-0301 · **Amount:** $1,240

Received March 3, 2026. Confirmed agreement: pay within 10 days, due March 13. World date: April 22. Owner funding already recorded.

Actual Claude and PostgreSQL, with a background loop polling every second for the test. Starting context is preloaded; all four new triggers are processed automatically. Only generated test data was used. Reminders and updates are internal records.

| Stage | Ledger paid | Positive unpaid alert | Payment-review findings |
| --- | ---: | ---: | --- |
| 1. Daily scan detects overdue invoice | $0.00 | $1,240.00 | No payment yet |
| 2. Management claims paid without proof | $0.00 | $1,240.00 | No payment yet |
| 3. Valid bank payment arrives | $1,240.00 | $0.00 | late_payment, statement_missing |
| 4. Supporting statement arrives | $1,240.00 | $0.00 | late_payment |

## 1. Daily scan detects overdue invoice

**Trigger:** `ev-monitor-scan-keyway-2026-04-22`

**Executed graph steps:** route → assess → payment_review → draft_alerts → verify → chase → finish

- **trail (llm)** — Winner: unpaid (sources disagree)
  Sources: doc-notice-riogrande-0420, doc-bank-kw-2026-03. Model: claude-sonnet-5-5. Tokens: 2071 in / 521 out.
- **cause (llm)** — Cause: unexplained. Oakline funded the full $1,240.00 on 2026-03-05, and the invoice was due 2026-03-13 (10 days from receipt under clause 7.2). The bank record shows no outgoing payment to the vendor and the vendor's 04/20 notice says it is unpaid; under the source-trust rules these outrank the manager's statement listing it as paid on 03/10. No holdback, paperwork hold, late-invoice issue or owner-side approval reason is on file, so the delay is unexplained.
  Sources: doc-funding-kw-0305, doc-bank-kw-2026-03, doc-notice-riogrande-0420, doc-stmt-kw-2026-03, doc-agreement-keyway. Model: claude-sonnet-5-5. Tokens: 3109 in / 333 out.
- **verifier (llm)** — Verdict: approved
  Sources: Template/schedule decision. Model: claude-sonnet-5-5. Tokens: 2830 in / 807 out.
- **chaser (rules)** — Next: polite reminder (tpl-polite)
  Sources: Template/schedule decision. Model: deterministic code. Tokens: 0 in / 0 out.

## 2. Management claims paid without proof

**Trigger:** `trace-management-paid-claim`

**Executed graph steps:** route → read_reply → assess → payment_review → draft_alerts → verify → chase → finish

- **chaser (llm)** — Claims payment with no vendor or bank proof: holding position
  Sources: Template/schedule decision. Model: claude-sonnet-5-5. Tokens: 1400 in / 124 out.
- **trail (llm)** — Winner: unpaid (sources disagree)
  Sources: doc-notice-riogrande-0420, doc-bank-kw-2026-03. Model: claude-sonnet-5-5. Tokens: 2161 in / 576 out.
- **cause (llm)** — Cause: unexplained. Oakline funded the full $1,240.00 on 2026-03-05, and the invoice was due 2026-03-13 under the 10-day term in clause 7.2. The bank record shows no outgoing payment to the vendor and the vendor's 04/20 notice says it is still unpaid, which outrank the manager's statement and reply that list it as paid without proof. No holdback, inspection, paperwork, vendor-side or owner-side reason is on file, so the delay is unexplained.
  Sources: doc-funding-kw-0305, doc-bank-kw-2026-03, doc-notice-riogrande-0420, doc-stmt-kw-2026-03, trace-management-paid-claim:INV-KW-0301, doc-agreement-keyway. Model: claude-sonnet-5-5. Tokens: 3226 in / 356 out.
- **verifier (llm)** — Verdict: approved
  Sources: Template/schedule decision. Model: claude-sonnet-5-5. Tokens: 2965 in / 453 out.

## 3. Valid bank payment arrives

**Trigger:** `ev-doc-trace-valid-bank-proof`

**Executed graph steps:** route → assess → payment_review → draft_alerts → verify → chase → finish

- **trail (llm)** — Winner: paid (sources disagree)
  Sources: trace-valid-bank-proof, doc-notice-riogrande-0420. Model: claude-sonnet-5-5. Tokens: 2257 in / 685 out.
- **cause (llm)** — Cause: unexplained. Oakline funded $1,240.00 on 2026-03-05, and the invoice was received 2026-03-03 with a due date of 2026-03-13 (10 days under clause 7.2). The bank record shows payment only on 2026-04-22, about six weeks after the 03/10 PAID line on the manager statement and after the due date. No holdback, paperwork, vendor-side or owner-side reason is on file, so the late payment is unexplained; the 03/10 PAID line has no bank support and the manager reply attached no proof.
  Sources: trace-valid-bank-proof, doc-bank-kw-2026-03, doc-funding-kw-0305, doc-inv-kw-0301, doc-stmt-kw-2026-03, doc-notice-riogrande-0420, trace-management-paid-claim:INV-KW-0301, doc-agreement-keyway. Model: claude-sonnet-5-5. Tokens: 3437 in / 419 out.
- **verifier (llm)** — Verdict: approved
  Sources: Template/schedule decision. Model: claude-sonnet-5-5. Tokens: 3210 in / 1102 out.

## 4. Supporting statement arrives

**Trigger:** `ev-doc-trace-supporting-statement`

**Executed graph steps:** route → assess → payment_review → draft_alerts → verify → chase → finish

- **trail (llm)** — Winner: paid (sources disagree)
  Sources: trace-valid-bank-proof, doc-notice-riogrande-0420. Model: claude-sonnet-5-5. Tokens: 2352 in / 684 out.
- **cause (llm)** — Cause: unexplained. Oakline funded $1,240.00 on 2026-03-05, and bank proof shows the full amount reached the vendor on 2026-04-22, about 40 days after the 2026-03-13 due date under clause 7.2. No holdback, paperwork hold, owner-side approval issue or late-invoice factor is on file to explain the gap, and the earlier 03/10 'paid' statement is not supported by the bank record through 04/05. The delay is unexplained; a controller may ask the manager to document the timing.
  Sources: doc-agreement-keyway, doc-funding-kw-0305, doc-stmt-kw-2026-03, doc-bank-kw-2026-03, doc-notice-riogrande-0420, trace-valid-bank-proof. Model: claude-sonnet-5-5. Tokens: 3524 in / 357 out.
- **verifier (llm)** — Verdict: refuted
  Sources: Template/schedule decision. Model: claude-sonnet-5-5. Tokens: 3312 in / 1403 out.

## Assertions

- PASS: Overdue scan raises exactly $1,240 unpaid
- PASS: First reminder recorded
- PASS: Unproven claim keeps $1,240 unpaid
- PASS: Chaser Claude reports a claim with no proof
- PASS: Ledger records $1,240 paid and no positive unpaid alert
- PASS: Bank proof flags late payment and missing statement
- PASS: Payment does not send another unpaid reminder
- PASS: Statement clears missing evidence while retaining historical lateness
- PASS: Idle polls create no additional decisions or database events
- PASS: All four stages include actual Claude calls
- PASS: No agent fell back to rules

**Live Claude decisions:** 13. **Result:** PASS.

The temporary worker was stopped and its test schema removed. Full event IDs, facts, rules, results and token receipts: [INVOICE_E2E_TRACE.json](INVOICE_E2E_TRACE.json).
