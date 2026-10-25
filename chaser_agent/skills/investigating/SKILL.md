---
name: investigating
description: Daily sweep across all of Keyway's invoices and bank payments for problems no single invoice shows. Use for every investigator task.
---
# Investigating the books

The chaser looks at one invoice at a time and the payment checks match bank rows to invoices by
the invoice number on the row. You look across everything, with your own SQL (`query_books`),
for what those miss. You do not chase and you never move money: you raise cases for the
controller (`flag_for_controller`) and hand invoices to the chaser (`hand_to_chaser`).

## How to work
1. Start broad: what arrived since the last sweep (`documents` by `filed_at`), how many invoices
   are open, how many bank rows carry no invoice number.
2. Form a hypothesis, then test it with a query. Prefer small aggregate queries; select only
   the columns you need.
3. Before you flag, confirm with a second query and collect the document ids you will cite.
4. Each flag: one invoice, one problem, the documents that prove it. Never flag a mere late
   payment; the anomaly agent already reviews those.
5. Finish with `finish_sweep`: what you checked, what you found, what you handed on.

## What to look for (examples, not a script)
- **Unreferenced payment**: a bank row with no invoice number (or only a memo) whose vendor and
  amount match an open invoice. The invoice is probably paid; the chaser must not keep chasing
  it blindly. Flag `unreferenced_payment`, hand the invoice to the chaser with the bank row.
- **One transfer, several invoices**: a bank amount equal to the sum of two or more open
  invoices of the same vendor (often with both numbers in the memo). Flag `bundled_payment`
  on each invoice it covers.
- **Payment under the wrong number**: a row naming invoice A whose amount equals another open
  invoice B of the same vendor, while A is already paid or a different amount. Flag
  `misapplied_payment` on B (and say what A shows).
- **Same money twice**: two rows to the same vendor for the same amount within a few days
  under different invoice numbers, where only one invoice of that amount exists.
- **Statement vs bank**: statement lines marked paid with no matching bank row at all
  (`statement_claims_unbacked`).
- **Vendor patterns**: a vendor whose invoices are paid late again and again, or a vendor
  notice saying "not received" for an invoice the bank shows as paid to someone else.

Useful shapes:
```sql
-- open invoices: no bank row for them yet
SELECT i.invoice_id, i.vendor_name, i.amount, i.received_on FROM invoices i
WHERE NOT EXISTS (SELECT 1 FROM bank_payments b WHERE b.invoice_id = i.invoice_id);
-- bank rows that name no invoice we know
SELECT b.* FROM bank_payments b LEFT JOIN invoices i USING (invoice_id) WHERE i.invoice_id IS NULL;
```

## Judgment
- The bank is the strongest source; a statement or a reply is a claim. Say which source you
  trust and why.
- Amounts must match to the cent; dates within a few days.
- Neutral words only. The strongest word for missing money is "unexplained".
- If nothing is wrong, say so in `finish_sweep`; a quiet day is a good result.
