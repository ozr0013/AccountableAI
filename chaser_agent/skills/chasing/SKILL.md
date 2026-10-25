---
name: chasing
description: Decide the next step for one vendor invoice that Oakline funded and Keyway (the management company) must pay. Use for every chaser task.
---
# Chasing a vendor payment

You own one invoice. Oakline funded Keyway; Keyway must pay the vendor by the deadline in the
vendor's agreement. Your job: make sure it gets paid, without nagging and without accusing.

## Read first
Call `get_invoice_context` once. It gives you the ledger line, the agreement rules (with the
quote each came from), every earlier chaser action and controller decision, the vendor's
payment history, any manager replies, and `policy`: the actions the approved schedule allows
today. Only actions listed in `policy.allowed` will be accepted.

## Decide (exactly one decision tool)
| Situation | Decision |
|---|---|
| A bank payment for this invoice exists (any amount) | `handoff_to_anomaly` - the anomaly agent checks amount, recipient and timing |
| Not past the deadline | `wait` until the day after the deadline |
| Overdue, a reminder draft is still awaiting approval or delivery | `wait` one day; mention what it waits on |
| Overdue, the last reminder shows `delivery: delivered` and policy allows `send_reminder` | `send_reminder` (the next level) |
| Overdue and `policy.allowed` includes `send_reminder` | `send_reminder` at the level policy gives |
| All three reminder levels delivered and still unpaid | `escalate` (owner + management notified, hold proposed, controller decides) |
| Agreement missing, unconfirmed or rejected | `wait` and say a controller must confirm the agreement |

## Reading the history
`status` is the chaser's own record (`queued` = handed to the outbox, `needs_approval` = waiting
for the controller). `delivery: delivered` (with a receipt reference) means the message actually
reached Keyway. `policy.allowed` already accounts for both; trust it over your own reading.

## Judgment calls
- A manager reply saying "paid" is not proof. Only a bank record is. Note the claim in your
  rationale and keep to the schedule.
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
