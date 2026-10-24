# Agreement agent

Agent 1 of 3 in the loop (agreement → chaser → anomaly detector; see the top-level README).
Reads agreements and invoices that arrive in the owner's Gmail (or a folder), checks they state
everything the owner needs to pay the vendor correctly, and escalates anything missing to the
owner. Anything that isn't an agreement or invoice (a resume, a flyer, a passport scan) is
skipped and never written to disk.

```
Gmail / folder ──▶ parser ──▶ LangGraph agent (tools) ──▶ check ──▶ escalate to the owner
                   text, header,   read_header, list_sections,      every required   drafted notice,
                   sections, kind  read_section, search,            field found?     never sent
                                   find_candidates, record_field,
                                   mark_missing, mark_unclear
```

**Checked fields.**
- Agreements: owner company name, vendor company name, payment due date or terms, payment amount or rate, and payment information (bank, routing and account, check payable to, remit-to). Effective date and signatures are reported but not required.
- Invoices: vendor, billed company, invoice number, amount due, due date or terms, and payment information.

**Guards.**
- `record_field` only accepts a value whose quote appears verbatim in the document, so the agent can't invent a due date.
- A field the agent never settles counts as missing.
- A scan with no text layer makes every field "unclear", never guessed.
- Escalations are drafts with status `drafted`. Gmail access is read-only.

## Run

```bash
python agreement_agent/samples/make_samples.py                 # sample PDFs + an .eml (fictional)
python -m agreement_agent --local agreement_agent/samples -v    # rules mode: free
python -m agreement_agent --local agreement_agent/samples/inbox # documents from an email
python agreement_agent/samples/make_batch.py                   # 12 fictional test documents + manifest.json
python -m agreement_agent --local agreement_agent/samples/batch # expected results are in batch/manifest.json
python -m agreement_agent --gmail                               # today's mail only (setup below)
python -m agreement_agent --gmail --sent-on 2026-10-24          # mail that arrived on that day
python -m agreement_agent --local FILE.pdf --model              # Claude drives the tools, about $0.03 per document
python -m agreement_agent.server                                # Sign in with Google + scan page on :8770
pytest -q agreement_agent                                       # offline tests (scripted model, Gmail stand-in)
```

Results land in `agreement_agent/out/` (gitignored): `reviews/*.json` and `escalations/*.json`.
Writing complete agreements into the shared Postgres database is planned, not built yet.

**One day of mail at a time.** A Gmail scan always reads a single sent-on day (default today):
the Gmail search is bounded to that day and each message's own timestamp is checked again. This
keeps a scan away from the rest of the inbox. To test, email the PDFs in `samples/batch/` to the
inbox and scan today.

**Sign in with Google.** `python -m agreement_agent.server` serves its own page at
`http://127.0.0.1:8770` and the JSON API the dashboard's **Agreements** screen uses (proxied as
`/agreements-api`). The login is the same `token.json` the CLI uses; "Sign out" deletes it.

**Rules or Claude.** Both run the same graph and the same tools. In rules mode, a fixed plan
calls the tools. With `--model`, Claude (Sonnet 5.5, `AGREEMENT_MODEL` to change it) decides which
tools to call: it reads the header, picks sections, searches again when a field isn't where it
expects, and marks a field unclear when the wording is ambiguous ("details to be provided
separately").

## Gmail setup (once)

1. In the Google Cloud console, enable the Gmail API and create an OAuth client of type "Desktop app".
2. Download it as `agreement_agent/credentials.json` (gitignored).
3. The first `--gmail` run opens a browser to sign in. The token is saved to `agreement_agent/token.json` (gitignored).

Default query: `has:attachment (filename:pdf OR filename:txt) (agreement OR contract OR invoice OR terms)`, limited to the sent-on day.

The OAuth app is in testing mode, so only accounts added as test users can sign in.
