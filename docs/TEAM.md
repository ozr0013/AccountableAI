# Team notes: how we built AccountableAI

The working notes from the hackathon: who owns what, how we worked without blocking each other, the task lists and checkpoints. The project itself is described in the [README](../README.md).

## Who owns what

| Person | Role | Folders |
|---|---|---|
| **Aaryan** | Rails and data world | `rails/`, `world/` |
| **Kanishka** (AI engineer) | Deterministic workflow + judgment agents | `workflow/`, `agents/agreement`, `agents/trail`, `agents/cause`, `agents/chaser`, `agents/case` |
| **Omar** | Verification, evals, memory briefing; Gmail agreement agent, anomaly detector | `agents/verifier`, `agents/briefing`, `evals/`, `agreement_agent/`, `agents/anomaly` |
| **Audrey** (frontend) | Dashboard, Judge Console UI, pitch | `dashboard/`, `docs/` |
| Shared | Data contracts, stand-ins, sample data | `contracts/` (PR only) |

Task lists per person: see **Task lists** below.

## How we work without waiting on each other

1. **Contracts first, together (Phase 0).** Data shapes and step signatures live in `contracts/`. Everyone builds against them, not against each other's code.
2. **Every dependency has a stand-in.** Until the real piece lands, use `contracts/fakes/`. Swap with an env var (`RAILS`, `AGENTS`, `VERIFIER`, `APPROVALS`, `NEXT_PUBLIC_FEED`).
3. **You own your folder.** Nobody edits another person's folder. `contracts/` is the only shared folder: small PR, approved by whoever it affects, **additive only** after Phase 0 (add fields, never rename or delete).
4. **Every workflow step is a plain function with a fixed signature.** Kanishka's workflow calls functions others own; stand-ins return canned results until then.

Blocked for more than 15 minutes? Stub it with fixture data, leave `# TODO(real): ...`, post in the team chat, keep going.

### Git workflow

- `main` always runs. Never push broken code to `main`.
- Branch per task: `<name>/<thing>` (e.g. `kanishka/cause-agent`, `omar/verifier`, `aaryan/ledger`, `audrey/case-queue`).
- Small PRs, merged every 2 to 3 hours. `git pull --rebase origin main` before opening.
- Merge your own PR into your own folder without review. `contracts/` changes need a thumbs up from whoever they affect.
- No secrets: keys go in `.env` (never committed). Template: `.env.example`.

```bash
git checkout main && git pull
git checkout -b omar/verifier
# ...work...
git add -A && git commit -m "verifier: block uncited claims"
git pull --rebase origin main
git push -u origin omar/verifier     # open a PR on GitHub
```

## Task lists

Each list starts with work that needs nobody. **NEEDS** items wait for a checkpoint.

### Progress (Sat)

| Person | Done | What's in `main` |
|---|---|---|
| Shared | Phase 0 | `contracts/` (models, step signatures, registry, stand-ins, scenario 3 fixture), `docker-compose.yml`, `.env.example`, `pyproject.toml` |
| Aaryan | 11 / 11 | `rails/` (event log, ledger, matcher, trust rules, recon, aging, `dependents_of()`, policy + accounting gates, Judge Console backend) and `world/` (one middleman, 20 vendors, all 9 scenarios + the moment, demo date Apr 22), plus the live loop in `rails/live/` (see **Live loop** below) |
| Omar | 8 / 8 | answer key, verifier, scorer + `evals.harness`, prohibited-action tests, briefing agent; 16-world test and planted bad drafts in `evals/` |
| Kanishka | 10 / 10 | `workflow/` (LangGraph graph, runner, replay, recheck), all five judgment agents, bank-detail path, sealed commit-reveal eval |
| Audrey | 9 / 10 | `dashboard/`: Next.js app on the live feed (`dashboard/feed/serve.py`, real workflow + Judge Console + approvals) with replay fallback; overview, open items + payment timeline, case queue, Keyway view, guardrails, Judge Console, briefing |

The scored evaluation artifacts, strong-model baseline, adversarial verifier study, guided live investigation, and Devpost draft are in the repository. The remaining deployment work is durable checkpointing and an authenticated production approval service; the hackathon demo intentionally uses a resettable in-memory session.

### Aaryan: rails and data world (`rails/`, `world/`)
1. [x] Event log (append-only, `valid_from` + `learned_at`, `supersedes`) and ingestion
2. [x] World generator, early version: scenario 3 plus ~100 clean invoices; expand to all 9 scenarios through the night (built: Oakline, one middleman (Keyway), 20 vendors, all 9 scenarios and the moment, Jan to Apr 2026; every planted item in `world/scenario_spec.json`)
3. [x] Funding ledger: per manager and vendor invoice, amount Oakline funded, amount paid to the vendor, due date (from confirmed terms)
4. [x] Matcher: vendor payments in the manager's bank records vs statement lines vs vendor confirmations; duplicate detection (scenario 8)
5. [x] Source-trust rules (bank > manager statement; vendor confirmation > manager "paid" claim)
6. [x] Reconciliation per manager: money funded = paid to vendors + authorized holds + still owed
7. [x] Aging and lateness pattern measured against the agreement's payment window (scenario 4)
8. [x] `dependents_of()` for selective recheck: build on a Postgres dependency table first, then mirror to Neo4j (memory with source links, briefing view) so recheck doesn't depend on Neo4j being up
9. [x] Policy check, accounting check, guardrails and gates (no money tool, approved-template reminders only, segregation of duties)
10. [x] Judge Console backend: presets + free text → events
11. [x] **NEEDS** Checkpoint A: Kanishka and Omar switch to `RAILS=real`. Rails side verified: on the full demo world, `RAILS=real AGENTS=real VERIFIER=fake python -m workflow.run --scenario 1 2 3 4 5 6 7 8 9 --clean 100 --funding keyway:18400` gives the 8 expected alerts and the $3,140 hold; `RAILS=real pytest evals/test_prohibited.py` passes; the workflow's in-memory rails answer exactly like Postgres (parity test). Omar: done (`python -m evals.harness --rails real`: 9/9 in rules mode; live Sonnet agents + Haiku verifier on Postgres in `evals/results_live_real.md`; `RAILS=real pytest evals/test_prohibited.py` passes). Left: Kanishka runs his flows on `RAILS=real` (load the matching world first: `python -m rails.load --fresh --scenarios … --clean …`) and tick this

### Kanishka: workflow and judgment agents (`workflow/`, `agents/agreement|trail|cause|chaser|case/`)
1. [x] Deterministic workflow (LangGraph): event → fixed next steps, checkpointing, replay without duplicates (all steps stand-ins first). Scored end to end by `evals/harness.py`. Still to do: Postgres checkpointer (in-memory today)
2. [x] Agreement agent: term sheets (vendor payment deadline, holdback rules, approval limit, allowed charges) with clause citations; human-confirm step
3. [x] Payment-trail agent: one timeline per item, conflicts resolved by source-trust rules (scenario 3)
4. [x] Delay-cause agent: five causes with evidence and confidence (scenarios 1, 5, 6 must produce no manager alert)
5. [x] Chaser agent: reminders to the middleman (polite, then firmer) from approved templates, logs replies; a "paid" reply counts only with vendor or bank proof (scenario 9)
5b. [x] Case agent: evidence pack and proposed hold amount for the controller
6. [x] Bank-detail change path (scenario 7)
7. [x] Selective recheck: rerun only `dependents_of()`; withdraw alerts that no longer hold (the moment)
8. [x] **NEEDS** Checkpoint A: swap stand-ins for real rails and verifier
9. [x] **NEEDS** Checkpoint B fallback: rerun all items for the affected manager and show which changed

### Omar: verification, evals, memory (`agents/verifier/`, `agents/briefing/`, `evals/`)
1. [x] Answer key for all 9 scenarios (start here; it's the spec) → `evals/answer_key.json`
2. [x] Verifier: refute drafts from evidence only; block uncited claims and accusatory wording → `agents/verifier/` (code rules first, then one model call; fails closed). Live (Haiku): 9/9 on the demo world; catches 8/8 planted bad drafts (`evals/adversarial.py`) vs 0/8 without it, $0.015
3. [x] Scorer: cause accuracy, alert precision/recall, citation rate, prohibited actions, withdrawal correctness, recon differences, cost/latency → `evals/scorer.py`; runs report in the `evals/schema.py` `RunResult` shape
4. [x] Baselines: single strong model with all documents (`evals/baseline.py`, `evals/baseline_results.md`); system without the verifier (`no_verifier` column in `evals.harness` and `evals.adversarial`)
5. [x] Prohibited-action tests (outbound send, money move, accusation, self-approval all blocked) → `evals/test_prohibited.py`; they run against whatever `RAILS`/`VERIFIER`/`APPROVALS` selects, so real rails must pass them too
6. [x] Briefing agent: "What's open with Keyway?" with citations and prior rulings → `agents/briefing/` (`python -m agents.briefing`; free code briefing, or `--model` for one Sonnet call, ~$0.04; uncited or accusatory claims dropped; `registry.briefer()`, `BRIEFING=real`)
7. [x] Checkpoint B: full scored run → `evals/results.md`; current free rules-mode re-run is 9/9 with 100% cause / precision / recall / citations
8. [x] Checkpoint C: final numbers surfaced in dashboard `/proof`, `evals/`, and `docs/DEVPOST.md`
9. [x] Messy data: `world/mess` renders PDFs, scans, emails and bank CSVs; `agents/extract` reads them back; `evals/extraction.py` scores it; `evals.harness --messy` runs the scenarios on parsed documents

### Audrey: dashboard and pitch (`dashboard/`, `docs/`)
1. [ ] Figma skeleton of every screen (skipped: built straight in code)
2. [x] Next.js app on the replay feed (`dashboard/public/feed/`, baked by `python dashboard/feed/export.py`): headline numbers, Keyway's open items sorted by vendor money still owed
3. [x] Payment timeline (one lane per source; trusted source marked, overruled claims struck) → `/items/[id]`
4. [x] Case queue: evidence pack, Approve / Decline (rails policy gate), audit trail, blocked outbound send visible → `/cases`, `/guardrails`
5. [x] Keyway view: funded vs paid vs owed, lateness by month, terms, aging, and the chase log → `/keyway`
6. [x] Judge Console UI showing what changed and what didn't (presets + free text; $3,140 → $2,240) → `/console`
7. [x] Ask-about-Keyway briefing panel → `/ask`
8. [x] Seats line, proof/evaluation visual, and Devpost draft → `/proof`, `docs/DEVPOST.md`
9. [x] **NEEDS** Checkpoint A: `NEXT_PUBLIC_FEED=live` (default; falls back to replay if the feed isn't running)
10. [x] Demo/video script → `docs/DEMO_SCRIPT.md` (recording remains a human presentation task)

### Phase 2 sponsors (after Checkpoint B only)

| Order | Add-on | Owner |
|---|---|---|
| 1 | Microsoft production slide | Audrey |
| 2 | Anthropic write-up (community, problem, failure modes) | Omar |
| 3 | CRS business credit + identity check on bank-detail changes (business data only) | Kanishka |

---

## Checkpoints (the only times we must sync)

| When | Checkpoint |
|---|---|
| Fri ~8:30 pm | **0: Contracts.** ✅ Done. Review `contracts/`, fill scenario 3, push; everyone branches |
| Fri ~10 pm | **Hyphenate booth check:** pitch the one-liner and the moment |
| Sat 9 am | **A: Stand-ins out.** Real rails, agents, verifier merged |
| Sat 2 pm | **B: First full scored run** on all 9 scenarios; keep selective recheck or use fallback |
| Sat 2 to 8 pm | **Phase 2 sponsors:** Microsoft slide, Anthropic write-up, CRS |
| Sat 8 pm | **C: Feature freeze,** Devpost draft |
| Sun 5 am | **D: Code freeze,** rehearse every Judge Console preset, record video |
| Sun 9 am | **Submit.** Judging 10:30 am to 12:30 pm, whole team at the table |

## Live loop: the system reacts by itself

`rails/live/` turns a fact into a reaction with no one pressing "run": a Judge Console fact is recorded at once, the workflow runs on it in the background, and what the system did is on the record. It is separate from the replay feed in `dashboard/feed/serve.py` and runs on Postgres.

```bash
python -m rails.load --fresh                    # the demo world (366 events, demo date Apr 22)
python -m rails.live warm                       # process what is there, once, before the demo
DEMO_TOKEN=... CORS_ORIGINS=http://localhost:3000 \
  uvicorn rails.console.api:app --port 8001     # ONE worker: the workflow runs one event at a time
```

| Endpoint | What it does |
|---|---|
| `POST /presets/{id}`, `POST /text` | A fact arrives (receipt date, bank line, a reply, ...). Recorded before the response; the workflow then runs in the background (`?wait=true` runs it first). |
| `POST /clock/advance` | Moves the demo clock to the **next deadline** (a reminder due or an invoice becoming overdue), or `{"days": n}` / `{"to": "YYYY-MM-DD"}`. Time only moves forward. |
| `POST /funding-review` | "Oakline is about to fund Keyway": the case agent proposes a hold ($3,140 of $18,400). |
| `GET /approvals`, `POST /approvals/{id}/decide` | The one human touch. The proposer and non-controllers are refused. |
| `GET /feed`, `GET /stats`, `GET /jobs/{id}` | What the system did and why, with citations; automatic actions vs human decisions; job status. Header `X-Demo-Token` when `DEMO_TOKEN` is set. |

Things to know before demoing:
- **Reminders are recorded as issued; nothing is emailed.** The clock is a demo clock (like a test clock): time moves only when asked.
- **Answers to identical model questions are reused** (`answer_cache` in Postgres), so a repeated "advance time" takes under a second instead of minutes. A fresh cache takes about 16 minutes to warm (30 invoices, live models); `TRUNCATE answer_cache;` gives a cold run.
- **Rehearse once, then reset:** `python -m rails.live script` plays the whole demo with timings, but it uses up the demo state (the moment is applied and the clock ends at Apr 27). Then `python -m rails.load --fresh` and `python -m rails.live warm` before the real demo. Never run `warm` or `script` while the API server is up.
- Agents and verifier follow `AGENTS` / `VERIFIER` (`real` = models, needs `ANTHROPIC_API_KEY`); `/stats` reports the mode.

## Messy data: the system reads real paperwork, not pre-parsed fields

The generator's documents arrive with `invoice_id`, `amount` and dates already filled in. Real AP
paperwork doesn't. `world/mess` renders the same world as what Oakline's controller actually
receives, and `agents/extract` has to read it back:

| Raw file | The mess in it |
|---|---|
| `inbox/*.eml`: vendor invoices forwarded by Keyway, PDF attached | 4 invoice layouts, vendor names spelled 3 ways, Keyway's reference written 6 ways or missing, invoice terms that contradict the agreement, an old balance added to the total, the same invoice forwarded twice, crooked phone scans with no text layer |
| `inbox/*.eml`: vendor confirmations, past-due notices, Keyway replies | amount only and no invoice number, one "we got paid" email covering two invoices, weekday dates ("hit our account Tuesday"), HTML-only mail, a bank-change request from a look-alike domain, the invoice named only in the quoted thread |
| `bank/*.csv`: Keyway's bank exports | truncated payee names, checks with no memo, one ACH paying two invoices, 90% paid with retainage held, fees, payroll and transfers mixed in |
| `drive/wires/*.pdf`: Oakline wire confirmations | one wire covering many invoices |
| `drive/contracts`, statements, junk | the agreement as a PDF, statements as PDF tables, a newsletter, an out-of-office reply, an estimate, a W-9, a credit memo, "statement attached" with nothing attached |

The parser is rules first (column positions, regexes, fuzzy vendor match against the vendor
master, then cross-document linking: wire remittance fills a missing reference, bank lines and
confirmations matched by vendor and amount, combined payments split by subset sum). Scans go
to tesseract if installed. A total that disagrees with Oakline's own wire for that invoice gets
one Haiku re-read of the page; still unresolved, it goes to a person. It never guesses.

**Result over 5 seeds:** rules only, 97.0% of documents exactly right (every miss is a flagged
scan or what depends on it); with OCR 99.5%; with OCR + Haiku 99.9% for $0.0012 total. All 9
scenarios and the moment still pass end to end on the parsed documents in every seed. Full
table and caveats: `evals/extraction_results.md`.

```bash
python -m world.mess --out out/raw                  # write the raw files (open them, they're real)
python -m evals.extraction                          # parse them back and score every field
python -m evals.harness --systems full --messy      # all 9 scenarios on parsed documents
EXTRACT_OCR=off EXTRACT_LLM=off ...                 # force rules only
```
