# CLAUDE.md

How this repo works, for anyone (human or Claude) changing it. The pitch and the full story are in
`README.md`; this file is the working map.

## What the system does

Three stakeholders: the **owner** (Oakline Homes) pays the **middleman** (Keyway Property
Management), who is supposed to pay the **vendors**. Three agents close the loop:

1. **Agreement agent**: an agreement arrives in the owner's Gmail → check it has both company
   names, the payment due date or terms, the amount, and the payment details → store it; if
   something is missing, tell the owner.
2. **Chaser agent**: after an invoice's due date, look for proof the vendor was paid (statement,
   bank record, vendor confirmation; the middleman's word alone never counts) → if none, remind
   the middleman again and again → after 3 days, tell the owner it is unpaid and tell the
   middleman the owner knows.
3. **Anomaly detector**: runs **only** when the middleman sends a financial statement → compare
   each line to the amount the owner approved for that invoice, to the cent → on a mismatch
   (approved $1,000, statement says $500) alert the owner and draft a note to the middleman.

Built vs planned (keep this honest when you change things):
- Gmail agreement agent writes reviews to `agreement_agent/out/`, **not** Postgres yet.
- Chaser escalates on the third reminder with at least 5 days between levels
  (`rails/guards/templates.py`); the "3 days, then tell the owner" rule is **planned**.
- Anomaly detector is built: alert for the owner, middleman note drafted, sent only after a
  controller approves it.

## Code map

| Path | What it is |
|---|---|
| `contracts/models.py` | Shared data shapes (Document, Event, LedgerLine, AlertDraft, AnomalyReport, ...). **Additive only**: add fields, never rename or delete |
| `contracts/steps.py` | Protocols every implementation follows: `Rails`, `Agents`, `Verifier`, `Approvals` |
| `contracts/fakes/` | Stand-ins for every protocol (no model, no database) |
| `contracts/registry.py` | Picks real vs fake by env var: `RAILS`, `AGENTS`, `VERIFIER`, `APPROVALS` = `real` \| `fake` |
| `workflow/` | The deterministic LangGraph workflow. `graph.py` (edges), `steps.py` (nodes), `state.py` (Route, WorkflowState), `runner.py` (`Workflow.ingest/replay`), `trace.py` (records each agent's reasoning for the live stream) |
| `agents/` | Judgment agents behind one facade, `agents/__init__.py:RealAgents` |
| `agents/agreement` | Workflow agreement agent: terms with clause citations, a controller confirms |
| `agents/chaser` | Reminders from approved templates; reads middleman replies with proof checks |
| `agents/anomaly` | Statement amounts vs approved amounts |
| `agents/trail`, `agents/cause`, `agents/case` | Code only, no model: what happened to a payment, why it's late, how much funding to hold. The chaser's steps |
| `agents/verifier` | Blocks alerts with uncited claims or accusing wording (rules first, then a model) |
| `agreement_agent/` | Standalone Gmail agreement agent: parser, LangGraph tool loop, Gmail connector, Sign in with Google server (`server.py`), CLI |
| `rails/` | Event log, ledger, matcher, trust rules, recon, policy gate (`rails/guards/policy.py`), templates |
| `world/` | Deterministic demo world (seed 13, demo date Apr 22 2026, 9 planted scenarios) |
| `ui-revamp-2/` | The frontend: static HTML/CSS/JS served by `dashboard/feed/serve.py` at its root. Every page is the designer's (`app.js`, her demo data) except Judgment › Agent Workflow, which `live.js` replaces with the live three-agent loop; leave `app.js` as is |
| `dashboard/` | The Next.js owner dashboard (`npm run dev`, :3000): Overview, Judge Console (feed the live engine, watch each agent it wakes), Review queue, Invoices, Properties; `dashboard/feed/serve.py` is the backend for both |
| `evals/` | Scorer, harness, baselines |

## How an event flows

`Workflow.ingest(event)` runs the graph once. `Steps._route_for` picks the route from the event
type alone; no model ever routes.

| Event | Route | Nodes |
|---|---|---|
| `document_ingested`, kind `agreement` | `agreement` | extract_terms → confirm_terms (pauses for a controller) → assess |
| `document_ingested`, kind `manager_statement` | `statement` | **detect_anomalies** → assess → draft_alerts → verify → chase |
| `document_ingested`, `status: bank_change_request` | `bank_change` | hold + identity check + controller approval |
| other documents, `fact_corrected`, `terms_confirmed` | `assess` | assess → draft_alerts → verify → chase |
| `chase_reply` | `reply` | read_reply → assess → ... |
| `scan_requested` | `scan` | assess every invoice of one manager, plus the lateness pattern |
| `funding_review_requested` | `funding_review` | case agent proposes a hold → controller |

Alerts: `draft_alerts` (code) → `verify` (verifier) → `alert_raised` / `alert_rejected` events. An
open alert whose facts no longer hold is withdrawn with a reason. Everything is an append-only
event in the rails; `workflow/book.py:WorkflowBook` folds the events back into state.

## How an agent is built

A model is used only where human text must be read: agreement, chaser (replies), anomaly
(free-text statements), plus the verifier. Trail, cause and case are `rules.py` only; keep them
that way. An agent that reads text follows this shape (see `agents/chaser`, `agents/anomaly`):
- `rules.py`: the free, deterministic version. It must work with no API key.
- `agent.py`: the model version. It calls `agents.llm.guarded_call(llm, Schema, SYSTEM, prompt,
  check)`: `check` lists problems (an id not in the documents, a quote not in the text) and the
  answer is retried once, then the rules result is used. A model never does arithmetic, routing
  or sending.
- `prompts.py`: the system prompt.
- `tests/`: offline, using `agents.testing.ScriptedLLM` for the model.
- Wire a new agent method into `contracts/steps.py:Agents`, `agents/__init__.py:RealAgents`,
  `contracts/fakes:FakeAgents` and `workflow/trace.py:TracedAgents` (so it shows in the live
  reasoning stream).

## Guardrails (never weaken these)

- No function moves money. `move_money`, `pay`, `wire`, ... exist only to be refused by `policy_check`.
- Messages to a middleman come only from controller-approved templates on the approved schedule;
  anything else outbound needs a controller to approve that exact draft (`send_outbound`).
- No alert without a cited record. No accusing words (`contracts/policy.py:BANNED_TERMS`): the
  strongest word is "unexplained".
- Agents recommend; only the controller approves or holds, and a proposer can't approve its own proposal.
- Every value an agent extracts must be quoted verbatim from the document.
- Gmail is read-only, and a scan reads one sent-on day only. Never widen a scan of a real inbox.
- Fictional companies only in data.

## Running things

```bash
source .venv/bin/activate
pytest -q                                   # everything offline, about 2.5 minutes
pytest -q agents/anomaly agreement_agent    # one area
ruff check .
python dashboard/feed/serve.py              # dashboard backend :8765 (rules mode, free)
cd dashboard && npm run dev                 # dashboard :3000
python -m agreement_agent.server            # Gmail agreement agent :8770
python -m workflow.engine                   # messy raw files -> alerts -> $3,140 -> $2,240 (free)
```

A running server keeps the code it started with: restart `serve.py` / `agreement_agent.server`
after changing Python code. Use `RealAgents` (rules) or real models for the demo, never
`AGENTS=fake`: the stand-in calls every late item unexplained. The demo world's statements all
match the ledger, so the anomaly detector stays quiet until a mismatching statement is ingested.

## Working rules

- Commits and PRs carry no AI co-author line or "Generated with" footer.
- Never commit `.env`, `agreement_agent/credentials.json`, `agreement_agent/token.json` or
  `agreement_agent/out/`. Never print the API key or the OAuth client secret.
- Before any paid model run, measure a tiny slice, quote the cost, and get a yes.
- You own your folder (see "Who owns what" in the README). `contracts/` changes are additive and
  agreed with whoever they affect.
- `main` always runs: run the tests before pushing to it.
