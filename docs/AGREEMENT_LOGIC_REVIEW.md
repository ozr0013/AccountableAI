# Agreement logic audit — 2026-10-24

Requested by the user and performed by a separate code-reviewer subagent against the local
`codex/vendor-chaser` worktree. Read-only audit: no functional edits, live API calls, or database
access. Existing deterministic agreement/terms tests: **28 passed**. Additional in-memory
reproductions exposed five high-priority findings and one medium-priority finding.

## High: removing a holdback preserves the old rule

Locations: `workflow/steps.py:74`, `workflow/steps.py:470`.

`_term_values` omits absent fields; confirmation compares only fields present in the proposal.
Reproduction: confirm a 10% holdback, then ingest an amendment removing it while retaining
deadline and approval limit. No confirmation pause occurs and the old 10% remains binding.

Required correction: compare and persist a complete rule state, including explicit removal.

## High: conditions and allowed charges are dropped

Locations: `workflow/steps.py:74`, `workflow/terms.py:24`, `workflow/payment_review.py:62`,
`agents/cause/rules.py:115`.

Reproduction: extract an owner-signoff holdback condition and an allowed fee. After confirmation,
the condition is `None` and allowed charges are empty. Payment review substitutes its inspection
assumption: a $900 payment against $1,000 receives a $100 holdback allowance because inspection
is pending, without establishing the contractual owner-signoff condition.

Required correction: persist all confirmed fields and share one payable-amount calculation
that evaluates the actual contractual release condition.

## High: confirmed rules lose their actual citations

Locations: `workflow/terms.py:8`, `workflow/terms.py:22`.

The confirmed-rule reader selects the first agreement document and reconstructs fixed clause
numbers. Reproduction: a payment term from clause 4.1 becomes a citation to clause 7.2 with no
quote after confirmation. Later amendments also point to the original agreement.

Required correction: retain each term's source ID, clause, quote and confirmation/version link.

## High: revisions lack effective-date and stale-approval protection

Locations: `workflow/steps.py:480`, `rails/ledger/build.py:32`.

Reproductions:

- Approve a newer 20-day proposal, then an older pending 15-day proposal: rules revert to 15 days.
- Append confirmed rules effective January 2027: an April 2026 invoice immediately receives
  the new 60-day deadline.

The ledger applies one current manager-wide rule to every invoice. Required correction:
version approvals, reject or rebase stale proposals, and select terms by effective date and scope.

## High: extraction guards permit wrong values and clause references

Locations: `agents/agreement/agent.py:43`, `agents/agreement/agent.py:63`.

Scripted-model reproduction: payment value 1 is accepted from a quote saying “ten (10)”,
because numeric validation uses substring matching. The payment quote can also be attributed
to an existing approval clause 6.2 instead of its actual payment clause 4.1, because quote
validation searches the whole document.

Required correction: validate numeric tokens and require the quote to belong to the cited clause.

## Medium: business-day semantics are unsupported

Locations: `contracts/models.py:96`, `rails/ledger/build.py:59`.

There is no calendar/business-day basis in the rule model; deadlines use calendar `timedelta`.
Rules extraction fails on “ten (10) business days”; scripted model extraction can return 10,
but loses the business-day meaning. Required correction: represent the basis explicitly and
calculate it correctly, or route unsupported deadline clauses to manual handling.

## Confirmed behavior and scope

- Ingestion routes agreement documents to extraction, records a proposal, and pauses for
  controller confirmation. Tests reject approval by `agreement_agent`.
- No initial confirmation bypass was reproduced. An unconfirmed agreement alone leaves the
  ledger deadline unset, so assessment does not initiate overdue chasing.
- PostgreSQL checkpoints are supported but not enabled by default; pending LangGraph
  confirmation state is currently in memory.
- Gmail/Outlook intake is not implemented in this Python harness. Ingestion expects a
  constructed `Document`; the monitor reads database events.
- Agreements are owner–property-manager scoped, keyed by `manager_id`. Vendor-specific
  agreement applicability is not represented.
- Allowed-charge validation is not implemented in payment review.

## Assessment

The demonstrated fixed-agreement chasing path works. Agreement amendments, conditional rules,
provenance and broader contract interpretation require the corrections above before expanding
the workflow. Passing the existing tests does not cover these reproduced failure cases.
