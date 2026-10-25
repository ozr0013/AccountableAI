# Agent API Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Verify api-convert agent APIs and expose their real persisted state and controller actions in the existing frontend.
**Architecture:** Keep existing FastAPI services and databases; add same-origin Next.js proxies for agreement8772/chaser8773, distinct from Google profile8771 and legacy demo feed8765. Add a typed browser client and one live agent-workflow page. Existing UI style and account page stay intact. New reads use real services; no mock fallback.
**Tech Stack:** FastAPI/Pydantic/Python, Next.js/React/TypeScript, PostgreSQL and SpacetimeDB.
**Spec:** User request and pasted chaser scenario (attachment f82a4e0a-a796-4367-892f-67878f613bf4); scope question pending for a new anomaly consumer. Existing API integration proceeds independently.

## Constraints and review focus
- Preserve existing SpacetimeDB records; do not run scenario_chaser.py because it clears shared chase history.
- No real mail/money movement; queued drafts must not be labeled delivered.
- Tokens remain in browser memory; forward only explicit controller Authorization headers, never Anthropic keys.
- Keep unrelated untracked .claude and .gstack files untouched.
- Wrong-case async updates, partial service failures, malformed dates/statuses, stale approvals and swallowed persistence errors need explicit handling/tests.

### Task 1: Verify and complete API read contracts
- [x] Add failing chaser API tests for invoice lists/detail, date preview, HTTP validation, auth and unavailable dependencies.
- [x] Add non-model invoice/preview endpoints using existing DataTools and deterministic checks; close owned Postgres connections; retain existing API contracts.
- [x] Verify agreement upload/confirm tests and live GET endpoints without modifying shared data.

### Task 2: Same-origin frontend integration
- [x] Add typed client and failing Node native tests for paths, query/body construction, auth, uploads, non-JSON errors and timeouts.
- [x] Add separate agreement-agent/chaser-agent rewrites and a live Agent workflow sidebar/page.
- [x] Display service health, agreement upload/rules/missing-term confirmation, invoice dry-run/results, pending approvals, reply reading, and Claude question/tool trace. Reads work without a controller token; gated writes use token.
- [x] Compile TypeScript/build and browser-test against running real services for reads and isolated test data for mutations.

### Task 3: End-to-end validation and handoff
- [x] Cover agreement->rules->overdue reminder->human firm approval->paid handoff in isolated in-memory API integration; document missing consumer honestly until scope decided.
- [x] Run Python/Node tests, independent Python/TypeScript/security review, fix findings, and verify scoped live Claude read-only answer.
- [x] Save API report, startup instructions and session continuity notes. Do not commit/push unless requested.

## Completion and rulings
- Existing agreement/chaser APIs integrated first; new anomaly consumer remains separate pending scope. UI explicitly labels unconsumed handoffs.
- Full Python439passed/3skipped; finalfocused53passed/1skipped; Node8passed; TypeScript+productionbuild passed.
- Independent TS/Python/security reviews found and verified fixes for optional confirmationfields, activeagreement rejection, changedcadence approval bypass and futurepersistedchecks.
- Real Claude agreement extraction verified with in-memory persistence; real Claude chaser HTTP lookup verified over live databases. SharedSpacetime history was not cleared or mutated by validation.
- Existing API services running8772/8773; new frontend3006/agent-workflow. No realmail. No commits/pushes requested for this task.
