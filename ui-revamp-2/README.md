# AccountableAI — UI Revamp 2

A standalone HTML, CSS, and JavaScript payment dashboard recreated from the five supplied Stitch UI screens.

## Connected to the live backend

`python dashboard/feed/serve.py` serves this folder at http://127.0.0.1:8765, next to its API. `live.js`
(loaded after `app.js`, which is unchanged) connects the two:

- The Keyway invoices from `GET /api/ui/invoices` replace the demo invoices, so every page shows the
  real workflow: properties from the invoice addresses, statuses, and the invoices a person should
  review (open alerts).
- **Agents**, the first page, runs the three agents one after another (agreement, chaser, anomaly
  detector) and streams each agent's reasoning into its card as it decides (`/api/loop/*`,
  `/api/stream`). The backend emails the middleman's reminders and statement note.

Opened any other way (the `python3 -m http.server` steps below), it talks to http://127.0.0.1:8765,
and falls back to the demo data when the backend isn't running.

## Push from your VS Code clone

1. Copy this entire `ui-revamp-2` folder into the root of your existing `RepoCalhacks13.0` clone.
2. Open a terminal in VS Code at the repository root.
3. Check that `git remote get-url origin` points to `ozr0013/RepoCalhacks13.0`.
4. Run:

```bash
git switch -c ui-revamp-2
git add -- ui-revamp-2
git commit -m "Add functional AccountableAI UI revamp 2" -- ui-revamp-2
git push -u origin ui-revamp-2
```

The commit is limited to the `ui-revamp-2` folder. It creates the branch from the currently checked-out commit. After the push, open a pull request on GitHub using the text in `PULL_REQUEST.md`.

## Run locally

From this folder:

```bash
python3 -m http.server 8000
```

Open `http://localhost:8000` in your browser. There are no build dependencies.

## Features

- Portfolio, property, invoice, judgment, settings, and help navigation.
- Status cards opening the matching filtered invoice ledger.
- Search by invoice ID, CID, vendor, and property.
- Property, vendor, status, and date filters; pagination and CSV export.
- Notifications linking to the two invoices needing review.
- Invoice review notes and reviewed status.
- Profile, photo, and preference editing saved to this browser.
- Responsive layouts and selectable chart periods.

## Demo data and limits

The October demo snapshot reconciles to total invoiced $1,428,500, payments $951,850, current outstanding $382,400, and past due $94,250. The ten-invoice dataset is sample data. Review actions change notification state without making payments.

This package is a standalone dashboard folder; it has not been integrated with the existing repository's framework or backend because that repository was unavailable to the development environment. Trend curves, category allocation, and agent progress are illustrative. Live accounting, email, payments, AI agents, and account security services are not connected. Profile details and review state use browser local storage.

The source files match the deployed prototype. Authentication on the private hosted Site is supplied by its hosting platform; this static package does not implement its own sign-in system.
