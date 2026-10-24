# Controller dashboard

Next.js app for Oakline's controller, on top of the real workflow.

```bash
# 1. live feed: the demo world replayed through the workflow (rules-mode agents, free)
source .venv/bin/activate && python dashboard/feed/serve.py      # http://127.0.0.1:8765

# 2. the app (Node 20+)
cd dashboard && npm install && npm run dev                       # http://localhost:3000
```

If the live feed isn't running, the app falls back to the **replay feed** (`public/feed/*.json`):
everything renders, and the Judge Console can play the moment, but approvals and other presets
need the live feed. `NEXT_PUBLIC_FEED=replay` forces replay. Re-bake the replay files after any
change to the world or workflow:

```bash
python dashboard/feed/export.py
```

## Screens

| Route | What it shows |
|---|---|
| `/` | Product homepage: glass hero, interactive evidence example, cursor-lit dot field, links into the app |
| `/overview` | The moment (hold before funding $18,400), funded / paid / owed, agent activity, flagged alerts, open items sorted by vendor money owed, seats line |
| `/items`, `/items/[id]` | One cause per open invoice; payment timeline with one lane per source, the trusted source marked and the overruled claim struck; cause, alerts with citations, evidence |
| `/cases` | Case queue: evidence pack, Approve / Decline through the rails policy gate, blocked actions, audit trail |
| `/keyway` | Funded vs paid vs owed (reconciles), lateness by month against clause 7.2, confirmed terms, aging, chase log with replies |
| `/guardrails` | Live calls to the policy gate: what agents tried and why it was refused |
| `/console` | Judge Console: presets and free text; what was rechecked, what changed, what didn't, alerts withdrawn, $3,140 → $2,240 |
| `/ask` | Briefing: "What's open with Keyway?" with cited claims and prior rulings |

Every citation chip opens its source document.

## Feed API (`feed/serve.py`)

`GET /api/state` · `GET /api/presets` · `POST /api/console {preset, params} | {text}` ·
`POST /api/approvals/{id} {approve, by}` · `POST /api/brief {question}` · `POST /api/reset`

The briefing is the free code briefing unless `DASHBOARD_BRIEF_MODEL=1` (one Sonnet call per
question, ~$0.04).

## Design

Reference palette: mineral gray (#F0EEED), charcoal (#161A1E), and apricot (#F97C37),
frosted panels, responsive approval controls, `/ SECTION ■` titles in mono caps, and diff lines (`- old` / `+ new`) wherever a
fact changed. Type is IBM Plex Sans and Mono. Tokens live at the top of `app/globals.css`.

## Interactive demo loop

- `/agents` shows agent responsibilities and activity.
- On `/keyway`, reply as Keyway with no attachment, a bank record, or vendor confirmation.
  An unsupported "paid" claim leaves the invoice open; payment evidence triggers a recheck.
- On `/console`, correct the electrician's received date. The unexplained balance changes
  from $3,140 to $2,240 and a new funding case supersedes the previous pending review.
- On `/cases`, approve the funding instruction or decline the hold. Bank-detail approval
  requires the identity-verification checkbox; a rejected attempt leaves the case pending.
- Sidebar controls advance the demo clock, reset the world, and switch Rules / Claude.
  Rules mode is the default and needs no credentials. Reset retains the process's spend meter.

Additional endpoints: `POST /api/reply {invoice_id, text, proof: "none" | "bank" | "vendor"}`,
`POST /api/clock {days}`, `POST /api/live {on}`. Approvals also accept `identity_verified`.

For Claude mode, supply `ANTHROPIC_API_KEY` to the feed process. `FEED_BUDGET` defaults to $1
and stops subsequent model calls once estimated usage reaches it. It is not a hard billing
cap: a final call can exceed it, and the price table must match your provider's rates.
Reading dashboard state always uses rules and never invokes a paid model. Live calls were
not needed for the regression tests; model availability depends on your configured provider.

Run the dashboard loop tests with `python -m pytest dashboard/feed/tests -q`.
This is a local, single-session demo with in-memory state and a simulated controller identity;
restarting the feed resets the world. It is not an authenticated production approval service.

## Reading the dashboard

The overview shows one funding decision, three totals, and at most three priority alerts.
Open a case's **Review evidence & decide** section to see the recommendation, source records,
and controller actions. Previous decisions, audit history, agent logs, vendor history, invoice
timelines, and source documents are collapsed by default. Native disclosure controls work
with a pointer or keyboard; no information is removed. Demo controls are in the sidebar's
expandable **Demo controls** panel. The open-items list links to each invoice's full detail.
