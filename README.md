# AccountableAI: make sure the middleman pays your vendors

**Cal Hacks 13.0 · Hyphenate track (core) · Team: Aaryan, Kanishka, Omar, Audrey**

A company pays a middleman, and the middleman is supposed to pay the vendors who do the work. Sometimes the middleman sits on the money. The vendor goes unpaid, stops working, and the company finds out too late. AccountableAI's agents watch every dollar the company hands a middleman, confirm each vendor actually got paid, chase the middleman when they didn't, and tell the company early, with proof.

> **Priority rule: Hyphenate first, sponsors later.** Until Checkpoint B (Sat 2 pm, first full scored run), nobody touches sponsor work.

---

## The pitch in one breath

*"When you pay a middleman, you can't see whether your vendors got paid until a vendor stops showing up. Our agents check every vendor payment, chase the middleman, and warn you before you send them more money."*

- **Market:** property management, the market of Rently (a named Hyphenate customer). We never say "built for Rently."
- **Parent company (fictional):** *Oakline Homes*, a rental owner with ~60 homes in 3 states.
- **Middleman (fictional):** one property manager, *Keyway Property Management*. Oakline sends it money for repairs and upkeep.
- **Vendors (fictional):** plumbers, electricians, HVAC, roofers, landscapers, cleaners. The middleman hires them and is supposed to pay them from Oakline's money.
- **Customer:** Oakline's controller. Today they get a monthly statement from Keyway saying "vendors paid" and can't check it against the bank, the vendors and the management agreement.
- **Seats filled:** *"The accounts payable reviewer who confirms every vendor got paid from the money we sent the middleman, and the collections person who chases the middleman when they didn't."*
- **The money flow:** Oakline funds the manager → the manager pays the vendor. Our system tracks every vendor invoice from "Oakline funded it" to "vendor confirmed paid." Tenants and rent are out of scope.
- **The moment:** Oakline is about to send Keyway next month's $18,400 repair funding. The system stops it: *"Keyway still owes 3 vendors $3,140 from March money you already sent. Chased 3 times. Recommend holding $3,140."* (Rio Grande Plumbing $1,240, Brightside Cleaning $1,000, Sparkline Electric $900.) A judge plays Keyway and says "we paid the electrician." The system checks: no vendor confirmation, no payment in Keyway's bank records, so it **holds its position and shows why**. Then the judge adds one real fact: Keyway's statement implied the electrician's invoice arrived Mar 28, but it only arrived Apr 15, and the agreement gives Keyway 10 days (due Apr 25). The system rechecks only that item, **withdraws that one alert and explains why**, and keeps the other two: $3,140 → $2,240.
- **Demo date:** Apr 22, 2026.

---

## Hyphenate's five hard parts

| Hard part | How we answer it | Proven by |
|---|---|---|
| **Sources that disagree** | Middleman says "paid," vendor says "not received," bank shows no payment; source-trust rules pick the winner and say why | Scenarios 3, 9 |
| **Time** (late in Jan to Mar changes how April is handled) | A middleman late in Jan to Mar gets flagged sooner and held to stricter terms in April; precedents; selective recheck on corrected facts | Scenario 4, the moment |
| **Working with others** | Agents chase the middleman, wait for replies, escalate when ignored; controller approves holds and can decline | Scenarios 2, 3, 6 |
| **Limits** | No money-moving tool; agents recommend a hold, only the controller holds; proposer can't approve; vendor bank-detail change held | Scenario 7 |
| **Explainable and accountable** | Every alert cites the funding record, the vendor's evidence and the agreement clause; verifier blocks uncited claims | "Why" trace, 100% cited |

Every unpaid vendor invoice gets one cause: **authorized hold**, **paperwork pending**, **vendor-side**, **owner-side**, or **unexplained**. Only "unexplained" and recurring patterns become alerts. The system defends the middleman when the evidence supports them.

---

## Architecture: deterministic code on top, agents only for judgment

```
 Documents / Judge Console ──▶ Ingestion (code) ──▶ Event log (Postgres, append-only)
                                                            │ each event triggers
 ┌──────────── LAYER 1: Deterministic workflow (code decides what runs next) ────────────┐
 └─────────────┬──────────────────────────────┬────────────────────────────┬─────────────┘
               ▼                              ▼                            ▼
   LAYER 2: code services          LAYER 3: judgment agents          Gates (code)
   funding ledger · matcher        agreement · payment-trail         policy + accounting checks
   aging · source-trust rules      delay-cause · chaser              no money-moving tool
   patterns · funded-vs-paid recon verifier · case · briefing        controller approves holds
               └──────────────────────────────┴────────────────────────────┘
                                              ▼
              Memory (Neo4j + event log): entity graph, two-timeline facts, precedents
                                              ▼
           Controller dashboard · case queue · chase log · Judge Console · eval harness
```

Models: Haiku for fuzzy matching, Sonnet for agreement / trail / cause / chaser / case / briefing, Opus for the verifier.

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
| **Omar** | Verification, evals, memory briefing | `agents/verifier`, `agents/briefing`, `evals/` |
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

### Progress (Fri night)

| Person | Done | What's in `main` |
|---|---|---|
| Shared | Phase 0 | `contracts/` (models, step signatures, registry, stand-ins, scenario 3 fixture), `docker-compose.yml`, `.env.example`, `pyproject.toml` |
| Aaryan | 10 / 10 before Checkpoint A | `rails/` (event log, ledger, matcher, trust rules, recon, aging, `dependents_of()`, policy + accounting gates, Judge Console backend) and `world/` (one middleman, 20 vendors, all 9 scenarios + the moment, demo date Apr 22) |
| Omar | 5 / 6 before Checkpoint B | answer key, verifier, scorer + `evals.harness`, prohibited-action tests, briefing agent; 16-world test and planted bad drafts in `evals/` |
| Kanishka | 3 / 7 | `workflow/` (LangGraph graph, runner, replay), payment-trail agent, delay-cause agent (LLM with rules fallback) |
| Audrey | 0 / 8 | not started in the repo |

Still open tonight: Docker on every laptop, the Anthropic API key in `.env`, the first live verifier run, the agreement / chaser / case agents, and an adapter from the workflow's output to the scorer's `RunResult`.

### Switching to the one-middleman world (Sat)

The demo world is now one middleman (Keyway), demo date Apr 22 (see **The demo world**). Nothing in `contracts/` or the rails API changed; only world data did, so this is test and id updates in your own folder. The old preset name `invoice_arrived` still works (alias of `invoice_received_correction`). Every planted item, with ids, amounts and dates, is in `world/scenario_spec.json`.

| Old | New |
|---|---|
| `INV-SB-0101`, `INV-PT-0201`, `INV-PT-0601`, `INV-SB-0701`, `INV-SB-0801`, `INV-SB-0802`, `INV-PT-0901` | `INV-KW-0101`, `INV-KW-0201`, `INV-KW-0601`, `INV-KW-0701`, `INV-KW-0801`, `INV-KW-0802`, `INV-KW-0901` |
| manager `sunbelt` / `peachtree` | `keyway` (the only manager) |
| `doc-agreement-sunbelt`, `doc-funding-sb-0101`, `doc-funding-pt-0201`, `doc-reply-pt-0901` | `doc-agreement-keyway`, `doc-funding-kw-0101`, `doc-funding-kw-0201`, `doc-reply-kw-0901` |
| scenario 7 vendor `v-voltwise` | `v-summit` (Summit Heating & Air, still $4,800) |
| `INV-KW-0402` = electrician paid Apr 8 | `INV-KW-0402` = the moment's **unpaid** Sparkline electrician ($900). The paid-on-time electrician (scenario 5) is `INV-KW-0501` (AmperPro, `doc-bank-kw-2026-04-s5`, `doc-conf-kw-0501`) |
| `MOMENT["value"]` `2026-04-01`, new due date Apr 11 | `2026-04-15`, new due date Apr 25 |
| moment keeps 1 alert | keeps 2: `INV-KW-0301` ($1,240) and `INV-KW-0901` ($1,000) |
| Keyway recon `diff == 0` | `diff == 320`: the flagged scenario 8 duplicate is on Keyway now (`diff - duplicate_funded == 0`) |
| scenario 4 pattern on `sunbelt` | on `keyway`: late Jan, Feb, Mar; April on time |

**Kanishka** (`agents/`, `workflow/`):
- [ ] `agents/cause/tests/test_cause.py`: ids and managers in the per-scenario table and the scenario 1 / 6 citation tests. `test_scenario_5_after_moment_is_vendor_side`: test vendor-side on `INV-KW-0501` (paid Apr 8, due Apr 11); `INV-KW-0402` is unpaid now
- [ ] `agents/chaser/tests/test_chaser.py`: `INV-PT-0201` / `INV-PT-0901` with `peachtree` → `INV-KW-0201` / `INV-KW-0901` with `keyway`; the proof test uses `INV-KW-0501`
- [ ] `agents/trail/tests/test_trail.py`: scenario 9 → `INV-KW-0901` / `doc-reply-kw-0901`; the paid-invoice test → `INV-KW-0501`
- [ ] `workflow/tests/test_scenarios.py`: ids; chases only for `keyway`; the scenario 4 pattern alert is for `keyway`
- [ ] `workflow/tests/test_bank_change.py`: `INV-KW-0701`, vendor `v-summit`
- [ ] `workflow/tests/test_memrails.py`: the paid-Apr-8 electrician is `INV-KW-0501`; Keyway recon check becomes `diff - duplicate_funded == 0`
- [ ] `workflow/tests/test_moment.py`: the withdrawal reason's new due date is `2026-04-25`; two alerts kept
- [ ] `workflow/run.py`: `--moment` help text says Apr 1 → Apr 15

**Omar** (`evals/`, `pyproject.toml`):
- [ ] `evals/answer_key.json` moment: `keep_count` 1 → 2 and update `_doc` (three items, $3,140 → $2,240, receipt Apr 15). Check the scenarios against `world/scenario_spec.json`
- [ ] `evals/test_harness.py::test_judge_moment` passes once `keep_count` is 2
- [ ] `pyproject.toml`: add `"world"` to `testpaths`

**Audrey** (`dashboard/`): one manager in the UI; preset id `invoice_received_correction`; demo numbers $3,140 → $2,240.

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
11. [ ] **NEEDS** Checkpoint A: Kanishka and Omar switch to `RAILS=real`

### Kanishka: workflow and judgment agents (`workflow/`, `agents/agreement|trail|cause|chaser|case/`)
1. [x] Deterministic workflow (LangGraph): event → fixed next steps, checkpointing, replay without duplicates (all steps stand-ins first). Scored end to end by `evals/harness.py`. Still to do: Postgres checkpointer (in-memory today)
2. [x] Agreement agent: term sheets (vendor payment deadline, holdback rules, approval limit, allowed charges) with clause citations; human-confirm step
3. [x] Payment-trail agent: one timeline per item, conflicts resolved by source-trust rules (scenario 3)
4. [x] Delay-cause agent: five causes with evidence and confidence (scenarios 1, 5, 6 must produce no manager alert)
5. [x] Chaser agent: reminders to the middleman (polite, then firmer) from approved templates, logs replies; a "paid" reply counts only with vendor or bank proof (scenario 9)
5b. [x] Case agent: evidence pack and proposed hold amount for the controller
6. [x] Bank-detail change path (scenario 7)
7. [x] Selective recheck: rerun only `dependents_of()`; withdraw alerts that no longer hold (the moment)
8. [ ] **NEEDS** Checkpoint A: swap stand-ins for real rails and verifier
9. [x] **NEEDS** Checkpoint B fallback: rerun all items for the affected manager and show which changed

### Omar: verification, evals, memory (`agents/verifier/`, `agents/briefing/`, `evals/`)
1. [x] Answer key for all 9 scenarios (start here; it's the spec) → `evals/answer_key.json`
2. [x] Verifier: refute drafts from evidence only; block uncited claims and accusatory wording → `agents/verifier/` (code rules first, then one model call; fails closed). Live (Haiku): 9/9 on the demo world; catches 8/8 planted bad drafts (`evals/adversarial.py`) vs 0/8 without it, $0.015
3. [x] Scorer: cause accuracy, alert precision/recall, citation rate, prohibited actions, withdrawal correctness, recon differences, cost/latency → `evals/scorer.py`; runs report in the `evals/schema.py` `RunResult` shape
4. [ ] Baselines: single strong model with all documents (not built); system without the verifier (done: `no_verifier` column in `evals.harness` and `evals.adversarial`)
5. [x] Prohibited-action tests (outbound send, money move, accusation, self-approval all blocked) → `evals/test_prohibited.py`; they run against whatever `RAILS`/`VERIFIER`/`APPROVALS` selects, so real rails must pass them too
6. [x] Briefing agent: "What's open with Keyway?" with citations and prior rulings → `agents/briefing/` (`python -m agents.briefing`; free code briefing, or `--model` for one Sonnet call, ~$0.04; uncited or accusatory claims dropped; `registry.briefer()`, `BRIEFING=real`)
7. [ ] **NEEDS** Checkpoint B: first full score → `evals/results.md`
8. [ ] **NEEDS** Checkpoint C: final numbers to Audrey

### Audrey: dashboard and pitch (`dashboard/`, `docs/`)
1. [ ] Figma skeleton of every screen (start now)
2. [ ] Next.js app on the replay feed (`contracts/fixtures/sample/`): headline numbers, Keyway's open items sorted by vendor money still owed
3. [ ] Payment timeline (one lane per source; trusted source marked)
4. [ ] Case queue: evidence pack, Approve / Decline, audit trail, blocked outbound send visible
5. [ ] Keyway view: funded vs paid vs owed, and the chase log
6. [ ] Judge Console UI showing what changed and what didn't
7. [ ] Ask-about-Keyway briefing panel
8. [ ] Seats line, Devpost visuals
9. [ ] **NEEDS** Checkpoint A: `NEXT_PUBLIC_FEED=live`
10. [ ] **NEEDS** Checkpoint C: record the video opening with the moment

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
cd dashboard && npm install && npm run dev     # Audrey sets this up (Next.js)
```

## Rules

All code written during the event. No secrets in the repo. Fictional companies only; real Hyphenate customers are named only as context in the pitch, never as data. The management agreement is the source of truth for payment terms.
