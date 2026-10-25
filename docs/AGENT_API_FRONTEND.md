# Agent API frontend integration

Branch: `api-convert`, based on `529d12f`. New page: `/agent-workflow`.

## What is connected

The page reads the actual agreement and chaser services, using same-origin Next.js proxies:

| Browser path | Service | Data |
|---|---|---|
| `/agreement-agent-api/*` | `http://127.0.0.1:8772/*` | Saved agreements, extracted rules, uploads, missing-term confirmation/rejection |
| `/chaser-agent-api/*` | `http://127.0.0.1:8773/*` | Invoices, previews, recorded actions, approvals, patterns, manager replies, Claude questions |

Override destinations with `AGREEMENT_AGENT_URL` and `CHASER_AGENT_URL` when building Next.js.
Google profile sign-in remains separate on 8771, through the existing account page. The older
investigation/review/demo screens retain their demo state; their clock controls are hidden on
the new live agent page. There is no API token: controller decisions (confirm, reject, approve,
evidence, delivery receipts, resolution) are plain requests recorded as made by `controller`.

Added read-only invoice list/detail endpoints and a no-write daily preview. Saved daily runs
reject dates beyond `rails.now()`; future dates are allowed for preview only. Backend approval
rechecks current payment evidence, deadline, cadence, level and message eligibility, excluding
the pending draft itself. Paid invoices, changed rules and future drafts cannot be approved.
Owned PostgreSQL connections are closed after each request. Dependency failures become
structured 502 responses. Repeated recorded handoffs no longer count as newly saved actions.
The new packages and tests are included in installation and default test discovery.

## Run locally

From `/Users/j_kanishkha/CALHACKS/RepoCalhacks13.0`:

```sh
source .venv/bin/activate
python -m agreement_agent.api
```

In a second terminal at the same root:

```sh
source .venv/bin/activate
python -m chaser_agent.api
```

These load `.env` (`ANTHROPIC_API_KEY` for Claude). No sign-in or token is needed.
The chaser's automatic loop stays off unless `CHASER_DAILY=on`; Run and save actions triggers an
explicit check. PostgreSQL and the configured SpacetimeDB database must be available.

In a third terminal:

```sh
cd dashboard
npm ci
npm run build -- --webpack
npx next start -p 3006
```

Open `http://localhost:3006/agent-workflow` or use the sidebar's Agent workflow link. This
integration does not require the older feed server for its live agent data. Google sign-in is
optional and does not authorize controller sign-off.

## Visual verification

1. Both service cards should say API reachable. Data loads automatically, independently for
   each service; failures remain visible and are never replaced with synthetic API data.
2. Select Rio Grande's agreement. Inspect payment deadline, payee/account rules and citations.
3. Filter vendor to Rio Grande and select `INV-KW-0301`. The chaser uses April 2 from the 30-day
   agreement; the ledger carries March 13. The page explicitly flags this discrepancy.
4. Click Preview decisions. It does not write records; future dates can be explored safely.
5. Use Run and save actions only when intentionally recording the current/historical check.
6. Supply the controller token to confirm missing required payment terms, reject an active or
   pending agreement with a note, or decide pending reminders. Optional missing metadata is
   displayed but not incorrectly submitted to confirmation.
7. Ask Claude a question. The UI exposes the answer, reasoning summary and selected tool trace.
   This is an explicit metered read-only model call. Reply reading records its result.
8. Queued does not mean delivered. No email transport or money movement is configured.

## Verified results

- Full Python suite: **439 passed, 3 skipped**.
- Final focused agreement/chaser tests: **53 passed, 1 skipped**.
- Browser client contract tests: **8 passed**; TypeScript and Next production build passed.
- Browser against live services: saved agreement/rules, invoice detail, deadline-discrepancy
  banner and no-write preview all passed.
- Real agreement Claude run: sample PDF, 8 field results, 4 rules, 4 model turns, complete review.
  Persistence was redirected to an in-memory SpacetimeDB fixture; shared agreements untouched.
- Real chaser Claude run: Opus selected `get_invoice`, cited the agreement deadline, identified
  missing vendor payment proof and correctly described the reminder as queued, not sent.
- Cross-agent HTTP fixture covers upload->rules->not-due preview->polite reminder->firm draft
  ->authenticated approval->bank payment->recorded anomaly handoff. No shared tables cleared.

Evidence: `docs/AGENT_API_VERIFICATION.json`.

```sh
python -m pytest -q
python -m pytest agreement_agent/tests chaser_agent/tests -q
node --experimental-strip-types --test dashboard/lib/agent-api.test.mjs
```

The scenario script `scripts/scenario_chaser.py` clears shared chase history while running. It
was deliberately not used for this verification; the new API fixtures isolate their state.

## Remaining workflow gap

`handoff_anomaly` is still a recorded row. This branch has no standalone consumer claiming it,
checking payment amount/payee/account/timing, recording review results or automatically
cancelling superseded drafts. The page labels this gap explicitly. This integration does not
claim that the full agreement->chaser->anomaly pipeline is complete.
