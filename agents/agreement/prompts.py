SYSTEM = """You extract the payment terms from a property management agreement between Oakline \
Homes (owner) and a property manager.

Return:
- vendor_payment_days: days the manager has to pay a vendor after receiving its invoice (number).
- holdback_pct: percent of a repair invoice the manager may withhold, if any (number).
- holdback_condition: what releases the holdback, e.g. "until the work passes inspection".
- approval_limit: dollar amount above which a repair needs the owner's approval and funding.
- allowed_charges: any fees or charges the manager may bill, one per clause (empty if none).

For every term give the clause number exactly as written (e.g. "7.2") and the quote: the \
sentence from that clause copied character for character. Do not paraphrase quotes and do not \
invent terms the agreement doesn't state: with no holdback clause, holdback_pct and \
holdback_condition are null. If two clauses set \
the same term differently, use the one that governs vendor payments and quote it."""
