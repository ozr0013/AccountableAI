# Finance workflow: executed events and backend map

Branch: api-convert; audit date: 2026-10-24.

## Execution scope

Two real Claude agreement reviews were executed through FastAPI TestClient. The actual agreement/chaser API handlers, graph, rule compiler, approval checks and daily decision logic ran against isolated in-memory SpacetimeDB fixtures and MemoryRails. Dates and bank ingestion were simulated. This proves the in-process HTTP workflow logic, not a new live PostgreSQL/SpacetimeDB, browser, scheduler, or delivery integration run. Shared payment history was not modified.

The earlier live Rio Grande upload separately verified persistence in the product database.

Agent suites rerun: 53 passed, 1 skipped.

## Executed events

| Event | HTTP | Observed output |
|---|---|---|
| No agreement: preview has no actionable decisions | 200 | No actionable decisions returned |
| Claude reviews incomplete GreenEdge agreement | 200 | mode=model; status=needs_controller; 8 fields; 4 rules; saved=True; estimated cost=$0.0316 |
| Controller fills GreenEdge missing terms | 200 | status=confirmed; controller=trace-controller |
| Controller rejects GreenEdge agreement | 200 | status=rejected; controller=trace-controller |
| Claude reviews complete Rio Grande agreement | 200 | mode=model; status=active; 8 fields; 4 rules; saved=True; estimated cost=$0.03548 |
| March 30: invoice not due | 200 | INV-KW-0301: not_due / recorded / - / owed $1,240.00 |
| April 22: overdue -> polite queued | 200 | INV-KW-0301: reminder / queued / polite / owed $1,240.00; new saves=1 |
| April 23: cadence -> wait | 200 | INV-KW-0301: wait / recorded / - / owed $1,240.00; new saves=0 |
| April 27: firm draft requires approval | 200 | INV-KW-0301: reminder / needs_approval / firm / owed $1,240.00; new saves=1 |
| April 28: pending approval -> wait | 200 | INV-KW-0301: wait / recorded / - / owed $1,240.00; new saves=0 |
| Controller approves firm reminder | 200 | status=approved; controller=trace-controller |
| May 2: escalation reminder requires approval | 200 | INV-KW-0301: reminder / needs_approval / escalation / owed $1,240.00; new saves=1 |
| Controller approves escalation reminder | 200 | status=approved; controller=trace-controller |
| May 7: owner-management escalation and proposed funding hold | 200 | INV-KW-0301: escalate / needs_controller / - / owed $1,240.00; new saves=1 |
| May 8: $1000 bank payment -> partial-payment anomaly handoff | 200 | INV-KW-0301: handoff_anomaly / recorded / - / owed $240.00; new saves=1 |
| Repeated check does not save duplicate handoff | 200 | INV-KW-0301: handoff_anomaly / recorded / - / owed $240.00; new saves=0 |
| Final action history | 200 | 5 action records |

## Event tree

```text
Document uploaded -> POST /review
  Invalid type, empty or >10 MB -> reject input
  No readable text -> mark fields unclear -> draft escalation
  Irrelevant document -> skip review work
  Agreement/invoice -> Claude tools loop when model=true
    Missing required fields -> draft owner escalation
    Missing critical payment terms -> blocking/pending rules -> needs_controller
      Controller confirms missing terms -> regenerate rules -> confirmed
      Controller rejects -> rejected rules
    Supported payment terms present -> active payment rules
  Save review/rules -> SpacetimeDB (save failures returned explicitly)
  Upload ends here; does not trigger a chaser check or create ledger invoice

Manual /chaser/run OR enabled daily loop -> read ledger + agreement rules + chase history
  Duplicate ledger line -> omitted
  No saved agreement -> skip (no request for missing agreement)
  Blocking/pending rules -> hold (no actual banking hold)
  Rejected rules / no active deadline / unknown receipt date -> skip
  Bank-paid amount > 0 -> record handoff_anomaly, including partial payments -> STOP
  No payment, today <= deadline -> not_due
  Overdue -> check history and reminder cadence
    Approval pending -> wait
    Cadence not elapsed -> wait
    First eligible reminder -> polite, queued
    Next eligible -> firm, needs_approval
    Next eligible -> escalation reminder, needs_approval
      Controller rejects -> wait cadence -> eligible for redraft
      Controller approves -> save decision (does not deliver message)
      Payment/rules changed before approval -> reject stale approval
    After escalation reminder + cadence -> escalation record, needs_controller
      Proposed owner/management notice and funding hold -> no external effect

Manager reply submitted -> POST /chaser/reply
  Read claim against existing proof -> record reply_read -> return verdict
  Does not itself ingest payment proof or trigger another daily check

Future payment-review agent / statement request / reconciliation -> NOT connected
```

## Backend mapping

- [Product upload and Claude checkbox](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/dashboard/app/agent-workflow/page.tsx:105)
- [Upload endpoint, validation, graph invocation and save](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/agreement_agent/api.py:109)
- [Claude prompt](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/agreement_agent/graph.py:35)
- [Claude tools and evidence guards](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/agreement_agent/graph.py:69)
- [Model turn, tool execution and routing](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/agreement_agent/graph.py:208)
- [Required-field checks and escalation drafts](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/agreement_agent/graph.py:257)
- [Rule compiler](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/agreement_agent/rules_from_terms.py:72)
- [Agreement and rule persistence](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/agreement_agent/api.py:95)
- [Confirmation and rejection API](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/agreement_agent/api.py:203)
- [Controller term validation](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/agreement_agent/controller.py:31)
- [Chaser background-loop startup](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/chaser_agent/api.py:55)
- [Chaser load, decide and idempotent persistence](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/chaser_agent/api.py:128)
- [Missing agreement, holds, payment and due-date branches](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/chaser_agent/daily.py:97)
- [Reminder cadence, approvals and escalation branches](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/chaser_agent/daily.py:130)
- [Approval revalidation](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/chaser_agent/api.py:275)
- [Manager reply API](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/chaser_agent/api.py:240)
- [Cross-agent API regression test](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/chaser_agent/tests/test_workflow_api.py:36)
- [Reminder and alternative branch tests](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/chaser_agent/tests/test_daily.py:28)
- [Agreement missing-term and controller tests](/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0/agreement_agent/tests/test_controller.py:62)

## Material gaps and mismatches

- No agreement-arrival event triggers the chaser; it reads saved rules at its next separate run.
- No-agreement invoices are skipped, not escalated into a document-acquisition case. Skips are counted but omitted from run decisions; no durable missing-document record is created.
- Invoice upload is a document review only; it does not create a ledger invoice. The trace started with a seeded ledger invoice.
- The chaser is deterministic daily decision code. Claude reviews documents and can answer read-only tool questions, but does not autonomously select daily actions.
- queued and approved are treated as delivered for cadence bookkeeping, but there is no email transport. Therefore dates in this scenario represent queue/approval progression, not actual delivery.
- An escalation message says Notified and proposes a funding hold, but the endpoint only records text. No notification or funding control is executed.
- Any positive payment exits chasing into handoff_anomaly, including partial payments. No consumer completes review or chases the unpaid balance afterward.
- The daily deadline currently always uses invoice_received; extracted from=invoice date and undisputed_only rule parameters are not applied here. Business days skip weekends only, not holidays.
- Amount, payee and bank-account rules are saved but not reconciled by this chaser; the intended consumer is missing.
- Ledger payment arrival is observed on the next check, not through an event subscription shown here. Pending drafts are blocked on attempted approval if payment arrived, but are not automatically cancelled.
- Human confirmation is limited to missing critical payment terms, not a general contract amendment workflow.
- Evidence validation permits values containing digits without proving all components appear in the attached quote. The prior live bank account example exposed this limitation.
- No durable graph checkpoint or full model message/tool-payload transcript is retained by the agreement review; result fields and step summaries are stored.

## Raw evidence

See FINANCE_WORKFLOW_TRACE.json for all 17 HTTP event responses, including both Claude review steps, field citations, generated rules, approval decisions, handoff and final history.
