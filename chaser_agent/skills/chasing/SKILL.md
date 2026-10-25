---
name: chasing
description: Decide the next step for one vendor invoice that Oakline funded and Keyway (the management company) must pay. Use for every chaser task.
---
# Chasing a vendor payment

You own one invoice. Oakline funded Keyway; Keyway must pay the vendor by the deadline in the
vendor's agreement. Your job: make sure it gets paid, without nagging and without accusing.

## Read first
1. Call `get_invoice_context` once: the ledger line (payments to this invoice's own vendor only),
   the agreement rules with their quotes, earlier chaser actions and controller decisions, the
   vendor's payment history, Keyway's statement claims, and `policy`: the actions the approved
   schedule allows today. Only actions listed in `policy.allowed` will be accepted.
2. Call `get_evidence` whenever the invoice is overdue, a payment exists, or any source claims
   payment: it lists every document (bank rows and which vendor each paid, vendor notices and
   confirmations, Keyway's replies, statement lines). Check the claims against each other.

3. Use `query_books` (read-only SQL over all the books) when the context leaves a question open:
   a bank row without an invoice number for this vendor and amount, other open invoices of the
   same vendor, how this vendor's past invoices were paid, or a note from the investigator. Say
   what you queried and what it showed. If the books show the money was likely paid under
   another number or without one, do not send a reminder blindly: flag it, and wait if policy
   allows.

## Memory
Each run starts fresh; your memory comes back in the context:
- `your_previous_runs`: what you decided on this invoice on earlier days and why. Continue from
  it: if you were waiting for something, check whether it arrived; do not repeat a flag you
  already raised; say what changed since last time.
- `lessons`: patterns any agent recorded about this vendor or Keyway, with their documents. Use
  them as leads and verify against the books; the books win if they disagree.
- `remember_lesson`: when you see a pattern worth knowing next time (Keyway's second unproven
  "paid" claim this month, a vendor that keeps reporting non-receipt), record it with the
  documents. Not for this invoice's own facts.

## Flag contradictions (before deciding)
If sources disagree, call `flag_for_controller` with the document ids, then still decide:
- a statement or a Keyway reply says "paid" but no bank row to this vendor exists
  (`statement_claims_unbacked`, `reply_without_proof`)
- the vendor says it was not paid while Keyway or the ledger says it was (`vendor_reports_unpaid`)
- a bank row for this invoice went to a different vendor (`payment_to_other_vendor`)
A flag is not a reminder and does not change the schedule; it puts the cited documents in front
of the controller. Flag each contradiction once; do not flag a mere late payment.

## Decide (exactly one decision tool)
| Situation | Decision |
|---|---|
| A payment for this invoice exists and it was not handed off yet | `handoff_to_anomaly` - the anomaly agent checks amount, recipient and timing; say whether the source is the bank or a vendor confirmation |
| Handed off already, but a balance is still owed (short or misdirected payment) | `send_reminder` for the balance when policy allows it |
| Not past the deadline | `wait` until the day after the deadline |
| Overdue, a reminder draft is still awaiting approval or delivery | `wait` one day; mention what it waits on |
| Overdue, the last reminder shows `delivery: delivered` and policy allows `send_reminder` | `send_reminder` (the next level) |
| Overdue and `policy.allowed` includes `send_reminder` | `send_reminder` at the level policy gives |
| All three reminder levels delivered and still unpaid | `escalate` (owner + management notified; a funding hold is proposed and only the controller can approve it) |
| Agreement missing, unconfirmed or rejected | `wait` and say a controller must confirm the agreement |

## Waiting
`wait` when policy also allows acting is a judgment call: say what you are waiting for. After
`policy.max_waits` days in a row (`policy.days_you_waited`) waiting is no longer offered and the
scheduled step is due.

## Reading the history
`status` is the chaser's own record (`queued` = handed to the outbox, `needs_approval` = waiting
for the controller). `delivery: delivered` (with a receipt reference) means the message actually
reached Keyway. `policy.allowed` already accounts for both; trust it over your own reading.

## Judgment calls
- A manager reply saying "paid" is not proof. Only a bank record to this invoice's vendor is.
  Flag it (`reply_without_proof`) and keep to the schedule.
- `manager_statements` show what Keyway's own statements claim. A statement that says "paid"
  while no bank payment exists is a contradiction the controller must see: say it plainly in the
  rationale, with the statement id, the claimed date and that no bank record backs it.
- A vendor that is chronically late (see history) is not a reason to skip levels; say it in the
  rationale so the controller sees the pattern.
- If the ledger due date and the agreement deadline disagree, the agreement wins; mention it.
- The reminder text is a controller-approved template. You do not write the message; you choose
  whether and when it goes, and you explain why.

## Rationale
Every decision takes a `rationale`: 1-3 neutral sentences naming the deadline, how overdue it
is, what was already sent, and why this step. Cite the rule id. Never say "fraud", "theft" or
"misuse"; the strongest word for missing money is "unexplained".
