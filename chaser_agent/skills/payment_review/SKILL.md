---
name: payment_review
description: Review a vendor payment the chaser handed off - amount, recipient, account, timing and the management statement - and record a verdict for the controller. Use for every anomaly task.
---
# Reviewing a payment

The chaser saw money leave for this invoice and handed it to you. Decide whether the payment is
right, and tell the controller exactly what is wrong if it is not.

## Read first
1. `get_payment_case` - the automatic checks' findings (each with a code), the bank rows, the
   statement, the agreement rules and whether the case is eligible to close.
2. `get_agreement_rules` only if you need the quote behind a rule.
3. `vendor_history` if lateness matters to your explanation.

## The checks (already computed in `findings`; you interpret them)
| Code | Means |
|---|---|
| `wrong_vendor` / `wrong_payee` | money went to someone other than the agreement's vendor |
| `wrong_payment_account` | bank, routing or account differs from the agreement |
| `missing_payment_identity` | the bank row does not say who or where it was paid |
| `partial_payment` / `overpayment` | amount differs from the approved invoice |
| `late_payment` | paid after the agreement deadline |
| `statement_mismatch` / `statement_predates_payment` / `statement_payment_unconfirmed` | the management statement does not back the bank payment |

## Verdict (exactly one `record_verdict`)
| Situation | verdict |
|---|---|
| No findings and the case is eligible to close | `clean` |
| Only the statement is missing or dated before the payment | `awaiting_statement` |
| Any money finding (recipient, account, amount) or lateness | `needs_review` |

The tool rejects a verdict the findings do not support (for example `clean` with open findings).

## Summary for the controller
`summary`: 2-4 neutral sentences: what was paid, to whom, when, against which deadline and
amount, what is wrong and what the controller should decide. List the finding codes you rely on
in `finding_codes`. Never accuse; "unexplained" is the strongest word.
