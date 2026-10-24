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
| `/` | The moment (hold before funding $18,400), funded / paid / owed, agent activity, flagged alerts, open items sorted by vendor money owed, seats line |
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

Warm neutral ramp, one accent (orange means it needs you, green means settled, wine is chrome),
hairline rules, `/ SECTION ■` titles in mono caps, and diff lines (`- old` / `+ new`) wherever a
fact changed. Type is IBM Plex Sans and Mono. Tokens live at the top of `app/globals.css`.
