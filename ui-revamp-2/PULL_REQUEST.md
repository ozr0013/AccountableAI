# UI revamp 2 — Add an interactive AccountableAI payment dashboard

Adds the five-screen AccountableAI UI as a standalone dashboard under `ui-revamp-2/`. The HTML, CSS, and JavaScript run without a build step.

- Connects navigation across Portfolio, Properties, Invoices, Judgment, Settings, and Help & Support.
- Opens filtered invoices from status cards.
- Adds invoice search, property/vendor/status/date filtering, chart tabs, pagination, and CSV export.
- Shows review notifications for Apex Roofing ($58,250) and Titan Climate ($36,000).
- Saves review notes, reviewed status, and profile preferences in browser local storage.
- Reconciles the portfolio snapshot to $382,400 current outstanding and $94,250 past due.

Validation: JavaScript syntax check passed. The standalone prototype was browser-tested for navigation, notifications, invoice reviews, search, date filtering, status filters, pagination, and saved settings. The existing repository's framework, backend, and CI have not been tested with this package.

Scope: Demo data and browser-local persistence. Trend curves, category allocation, and agent progress are illustrative. Live accounting integrations, payment processing, email, AI agents, and authentication are not implemented in the static package.
