# AccountableAI: make sure the middleman pays your vendors

**Cal Hacks 13.0 · Hyphenate track (core) · Team: Aaryan, Kanishka, Omar, & Audrey**

An owner pays a middleman, and the middleman is supposed to pay the vendors who do the work. Sometimes the money doesn't reach the vendor, or not all of it does. The vendor goes unpaid, stops working, and the owner finds out too late. AccountableAI closes that loop with three agents: one reads the agreement, one chases the payment, one checks the middleman's statements.

> **Priority rule: Hyphenate first, sponsors later.** Until Checkpoint B (Sat 2 pm, first full scored run), nobody touches sponsor work.

---

## Three stakeholders, three agents, one closed loop

| Stakeholder | In the demo | What they need |
|---|---|---|
| **Owner** | *Oakline Homes* (fictional), ~60 rental homes in 3 states | Know every vendor got paid, in full, on time, from the money it sent |
| **Middleman** | *Keyway Property Management* (fictional) | Receives the owner's money and pays the vendors |
| **Vendor** | 20 fictional plumbers, electricians, roofers, cleaners... | Get paid what the agreement says, when it says |

The owner and the vendors are the ones getting hurt today: the owner only sees the middleman's statement, and the vendor only sees that nothing arrived.

```
 Owner's Gmail ──▶ 1. AGREEMENT AGENT ──▶ database: agreement, companies, amount, due date, payment details
                         │ something missing?
                         └──▶ owner is told what's missing
                                      │
                                      ▼ (due date passes)
                    2. CHASER AGENT: did the middleman send a statement showing the vendor was paid?
                         │ no ──▶ reminds the middleman, again and again
                         │        └─ after 3 days: owner is told "not paid"; middleman is told the owner knows
                         │ yes
                         ▼
 Middleman's statement ─▶ 3. ANOMALY DETECTOR: does each amount match what the owner approved?
                         │ mismatch (approved $1,000, statement shows $500)
                         └──▶ owner is alerted; middleman gets a note that the amount is wrong
```

### 1. Agreement agent: is the agreement complete?

Triggered when an agreement arrives in the owner's Gmail. It reads the document (PDF or text, header first, then each section) and checks for the owner and vendor company names, the payment due date or terms, the amount or rate, and how to pay (bank, routing and account, check payable to, remit-to). A complete agreement is stored with the agreement name, the companies, how much the owner pays the vendor, the due terms and the payment details. If anything is missing or unclear, the owner gets a drafted notice listing exactly what, with nothing sent automatically. Every value it records must be quoted verbatim from the document.

### 2. Chaser agent: was the vendor paid by the due date?

Reads the stored terms, works out each invoice's due date, and once it passes, looks for proof the vendor was paid: the middleman's statement, the middleman's bank record, or the vendor's own confirmation. A middleman's word alone ("we paid it") is never proof. No proof: it reminds the middleman from approved templates, escalating polite → firm → escalation. If it is still unpaid **3 days** after the first reminder, the owner is told the payment hasn't been made and the middleman is told the owner has been notified.

### 3. Anomaly detector: does the statement match what we approved?

Triggered **only** when the middleman sends a financial statement. Every line is compared, to the cent, with the amount the owner approved for that invoice. A line that differs (approved $1,000, statement says $500) or names an invoice the owner never approved becomes an alert for the owner, with a note to the middleman saying which line is wrong. The comparison is code; a model is only used to read a statement that arrives as free text, and only figures printed verbatim in the statement are accepted.

### What is built today, and what is still planned

| Piece | Status | Where |
|---|---|---|
| Agreement agent: Gmail sign-in (read-only), PDF parsing, field checks, owner escalation draft | Built | `agreement_agent/`, dashboard **Agreements** page |
| Agreement agent: extract terms with clause citations, controller confirms, stored as events in Postgres | Built | `agents/agreement/`, `workflow/` |
| Agreement agent: a complete Gmail agreement written straight into the database | **Planned**: the Gmail agent saves its review to `agreement_agent/out/`, not Postgres yet | |
| Chaser: due dates from the terms, proof check, approved reminder ladder | Built (reminders are recorded, not emailed) | `agents/chaser/`, `workflow/steps.py` |
| Chaser: "owner told after 3 days, middleman told the owner knows" | **Planned**: today the escalation is the third reminder, at least 5 days apart (`rails/guards/templates.py`) | |
| Anomaly detector: statement amount vs approved amount, owner alert, drafted note to the middleman | Built; the note goes out only after a controller approves that exact draft | `agents/anomaly/`, `workflow/steps.py` (`detect_anomalies`) |

Behind the three agents, the workflow also runs supporting checks that existed before the three-agent plan: the payment-trail and delay-cause agents (what really happened to a payment, and why it is late), the verifier (blocks any alert without a cited record or with accusing wording), duplicate-billing and recurring-lateness checks, and the case agent (how much of the next funding to hold).

---

## The demo moment

Oakline is about to send Keyway next month's $18,400 repair funding. The system stops it: *"Keyway still owes 3 vendors $3,140 from March money you already sent. Chased 3 times. Recommend holding $3,140."* (Rio Grande Plumbing $1,240, Brightside Cleaning $1,000, Sparkline Electric $900.) A judge plays Keyway and says "we paid the electrician." The system checks: no vendor confirmation, no payment in Keyway's bank records, so it **holds its position and shows why**. Then the judge adds one real fact: Keyway's statement implied the electrician's invoice arrived Mar 28, but it only arrived Apr 15, and the agreement gives Keyway 10 days (due Apr 25). The system rechecks only that item, **withdraws that one alert and explains why**, and keeps the other two: $3,140 → $2,240. Demo date: Apr 22, 2026.

- **Market:** property management, the market of Rently (a named Hyphenate customer). We never say "built for Rently."
- **Customer:** the owner's controller, who today gets a monthly statement saying "vendors paid" and can't check it against the bank, the vendors and the agreement.
- **Seats filled:** *"The accounts payable reviewer who confirms every vendor got paid, and the collections person who chases the middleman when they didn't."*

## Hyphenate's five hard parts

| Hard part | How we answer it | Proven by |
|---|---|---|
| **Sources that disagree** | Middleman says "paid," vendor says "not received," bank shows no payment; source-trust rules pick the winner and say why. A statement amount that disagrees with the approved amount is caught by the anomaly detector | Scenarios 3, 9 |
| **Time** (late in Jan to Mar changes how April is handled) | A middleman late in Jan to Mar gets flagged sooner and held to stricter terms in April; selective recheck on corrected facts | Scenario 4, the moment |
| **Working with others** | The chaser reminds the middleman, waits for replies, escalates when ignored; the controller approves holds and outbound notes and can decline | Scenarios 2, 3, 6 |
| **Limits** | No money-moving tool; agents recommend a hold, only the controller holds; proposer can't approve; vendor bank-detail change held | Scenario 7 |
| **Explainable and accountable** | Every alert cites the funding record, the vendor's evidence and the agreement clause; the verifier blocks uncited claims | "Why" trace, 100% cited |

Every unpaid vendor invoice gets one cause: **authorized hold**, **paperwork pending**, **vendor-side**, **owner-side**, or **unexplained**. Only "unexplained", recurring patterns and statement mismatches become alerts. The system defends the middleman when the evidence supports them.

## Architecture: deterministic code on top, agents only for judgment

```
 Owner's Gmail / documents / Judge Console ──▶ Ingestion (code) ──▶ Event log (Postgres, append-only)
                                                            │ each event triggers
 ┌──────────── Deterministic workflow (LangGraph; code picks every edge) ─────────────────┐
 │ agreement ─▶ extract terms ─▶ controller confirms                                      │
 │ statement ─▶ anomaly detector ─▶ assess                                                │
 │ any fact  ─▶ assess (trail + cause) ─▶ draft alerts ─▶ verifier ─▶ chaser              │
 └────────────────────────────────────────────────────────────────────────────────────────┘
        code services: ledger · matcher · trust rules · recon · aging · patterns
        gates (code): policy + accounting checks · no money-moving tool · controller approves
                                              ▼
           Owner dashboard · alerts · chase log · approvals · Judge Console · eval harness
```

Models: Sonnet for the agreement, trail, cause, chaser, case and briefing agents and for reading free-text statements; Opus for the verifier. Every agent has a free rules mode that runs with no API key.

## The 9 planted scenarios (the answer key everyone builds to)

| # | Planted situation | Correct behavior |
|---|---|---|
| 1 | Oakline funded a $2,000 roof repair; manager holds 10% until inspection passes, as the agreement allows | Authorized hold. **No alert** |
| 2 | Vendor unpaid because their W-9 / invoice is missing | Paperwork pending. Draft request for the missing document |
| 3 | Statement says plumber paid Mar 10; plumber's Apr 20 past-due notice says unpaid; manager's bank shows no payment | Sources disagree. Bank + vendor beat the statement. **Unexplained alert** citing all three; chaser starts |
| 4 | Keyway pays vendors past its agreement's payment window in 3 of 4 months (Jan to Apr 2026) | Recurring-pattern alert with history; recommend stricter terms |
| 5 | Electrician (AmperPro, not the moment's Sparkline) paid Apr 8; invoice arrived Apr 1 (agreement: pay within 10 days of invoice) | Vendor-side, on time under the terms. **No alert on the manager** |
| 6 | HVAC replacement over approval limit unpaid because Oakline never funded it | Owner-side. **No alert on the manager**; flag Oakline's pending approval |
| 7 | Vendor asks to change bank details before a large payment | Hold for identity verification and controller approval |
| 8 | Same landscaping invoice billed to Oakline in two months | Duplicate flagged |
| 9 | After being chased, manager replies "paid"; no vendor confirmation, no bank payment | Hold position. **Keep the alert**, cite why the reply isn't enough |

Scenario 3 is the Phase 0 fixture in `contracts/fixtures/sample/`. Every planted item (ids, amounts, dates, expected cause and alert) is in `world/scenario_spec.json`, generated by `python -m world.spec`.

## The demo world (what everyone builds to)

One parent, one middleman, one demo date. Generated by `world/` (seed 13, deterministic); load it with `python -m rails.load --fresh`.

- **Oakline Homes** funds **Keyway Property Management**, which pays **20 vendors**. ~100 clean background invoices, Jan to Apr 2026.
- **Demo date: Apr 22, 2026** (`world.generator.WORLD_TODAY`). Nothing in the world is dated after it.
- **Agreement:** pay vendors within 10 days of receipt (7.2), 10% holdback until inspection (7.4), $5,000 approval limit (8.1).
- **Scenario 4:** Keyway pays past its own 10-day window in Jan, Feb and Mar; April is on time. The baseline is the agreement, not other managers.

The moment, item by item:

| Item | Vendor | Amount | Why it's open on Apr 22 |
|---|---|---|---|
| `INV-KW-0301` (scenario 3) | Rio Grande Plumbing | $1,240 | Statement says paid; bank and vendor say no |
| `INV-KW-0901` (scenario 9) | Brightside Cleaning | $1,000 | Keyway replied "paid", no bank or vendor proof |
| `INV-KW-0402` (the moment) | Sparkline Electric | $900 | Funded in March; statement implies received Mar 28, so due Apr 7 |
| **Total unexplained** | | **$3,140** | |

Judge Console preset `invoice_received_correction` (or free text "the electrician's invoice arrived Apr 15") supersedes Mar 28 with Apr 15. `dependents_of()` returns only `INV-KW-0402`, its due date moves to Apr 25, that alert is withdrawn, the other two stay: **$3,140 → $2,240**. Replay it with `python -m rails.console.moment`.

Reconciliation: funded = paid + authorized holds ($200) + still owed ($8,590) + the flagged scenario 8 duplicate ($320).

Run the demo with the rules agents (`RealAgents`, no API key needed) or the live ones, not `AGENTS=fake`: the stand-in calls every late item unexplained and recommends holding far more than $3,140.

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

## Guardrails (code, shown in the demo)

Never accuse (templates top out at "unexplained"; no "fraud"/"misuse" in the UI) · never move money · reminders to a middleman only from controller-approved templates, on an approved schedule; anything else outbound needs approval of that exact draft · agents recommend holds, only the controller holds · never alert without a cited record · append-only history · proposer can't approve · books must agree (balanced, no closed-month posting, no duplicates after replay).

## Proof

Baselines: (1) one strong model with all documents; (2) our system without the verifier. Metrics: delay-cause accuracy, alert precision (no false alerts on scenarios 1, 5, 6), holding position on scenario 9, recall, 100% cited claims, 0 prohibited actions, correct withdrawal after a changed fact, funded minus paid reconciles for Keyway, cost and latency per statement. Report failures too.

---

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

## Run it

```bash
cp .env.example .env            # add ANTHROPIC_API_KEY
docker compose up -d            # postgres + neo4j
python -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'
pytest -q                                           # all tests, offline (Postgres tests skip without docker)
set -a; source .env; set +a; LIVE=1 pytest -s agents/verifier -k live   # one real verifier call
RAILS=fake AGENTS=fake VERIFIER=fake python -m workflow.run --scenario 3
python -m rails.load --fresh                        # load the demo world into Postgres (+ Neo4j)
python -m rails.console.moment                      # replay the moment: $3,140 -> $2,240
pytest -q world                                     # world tests (not in testpaths yet)
python -m evals.run evals/runs/full.json evals/runs/no_verifier.json --out evals/results.md
RAILS=real pytest evals/test_prohibited.py          # guardrails against the real rails
python dashboard/feed/serve.py                      # live feed for the dashboard (free, rules mode)
python -m agreement_agent.server                    # Gmail agreement agent on :8770 (Sign in with Google)
python -m agreement_agent --local agreement_agent/samples/batch   # check the 12 test documents, free
cd dashboard && npm install && npm run dev          # dashboard on :3000 (see dashboard/README.md)
```

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

## Rules

All code written during the event. No secrets in the repo. Fictional companies only; real Hyphenate customers are named only as context in the pitch, never as data. The management agreement is the source of truth for payment terms.
