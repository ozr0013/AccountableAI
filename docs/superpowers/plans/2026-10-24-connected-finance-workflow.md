# Connected finance workflow implementation plan

Goal: connect product intake, saved agreement rules, monitoring, payment review, evidence requests, statement reconciliation and human closure on api-convert.
Architecture: preserve agreement/chaser APIs and ledger, add an event-sourced coordinator using the existing PostgreSQL event log. Idempotent projections and outbox actions recover on a polling tick; API mutations trigger immediate reconciliation. No new database or Spacetime schema migration required. Communication remains a durable product outbox until delivery is explicitly acknowledged; no claim of email delivery. Gmail intake excluded per existing user scope.
Ruling: user explicitly asked to connect the previously audited chain, so proceed using that accepted event flow without another design approval. Existing modified checkout is the authorized branch; preserve all prior changes and do not commit until requested.

- [ ] Backend: failing end-to-end and failure/replay tests; implement coordinator, typed evidence intake and case/outbox APIs with controller authorization; agreement save wake hook; scheduler polls by default and exposes errors.
- [ ] Product: extend typed API, case timeline, evidence intake and outbox acknowledgment/controller resolution on existing agent-workflow page. Worker owns frontend files only.
- [ ] Verify: Python and TypeScript/security reviews, full suite, build, real PostgreSQL restart/replay plus frontend browser check. No external mail sent during validation.
- [ ] Document exact commands, event tree, tested cases and remaining transport configuration.

Backend API contract for frontend:
GET /workflow/cases -> {cases:[{invoice_id,vendor,state,findings:[{code,message}],history:[{id,kind,at,payload}],outbox:[{id,invoice_id,kind,audience,body,status,created_on}]}],worker:{enabled,last_error,last_run}}
POST /workflow/tick -> {cases:[...],worker:{...}}
POST /workflow/evidence (controller bearer) -> {document_id,cases:[...]}; input {kind:'vendor_invoice'|'manager_bank'|'manager_statement',invoice_id,vendor_id?,amount:number,on:'YYYY-MM-DD',payee?,account?,routing?,bank?,reference?,invoice_date?}. Vendor invoice requires known vendor_id, bank/statement invoice must exist. Server generates stable document identity from content.
POST /workflow/outbox/{id}/ack (controller bearer) -> {status:'delivered'}; input {reference:string} documenting external delivery. Must never imply actual sending.
POST /workflow/cases/{invoice_id}/resolve (controller bearer) -> case; input {note:string}. Only fully paid with reconciled statement and verified evidence may close; findings can be acknowledged with note except missing evidence or unpaid amount.
Agreement API response gains workflow:{triggered:boolean,error?:string}; chaser run gains cases. Existing endpoints remain compatible. UI should fetch cases with graceful error, poll every15s when idle, default Claude enabled, remove obsolete unimplemented-consumer warning.
