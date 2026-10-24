# AccountableAI — Devpost draft

## Inspiration

When a company funds a property manager, it often only learns a vendor was not paid once work stops. A monthly statement saying “paid” is not proof. We wanted to make every funded dollar explainable before the next funding instruction goes out.

## What it does

AccountableAI follows funding from Oakline Homes through Keyway Property Management to each vendor. It reconciles invoices, funding records, manager statements, bank records, vendor notices, and manager replies. Agents build a payment trail, identify the reason an invoice is unpaid, challenge their own alerts, chase missing proof, and propose a hold. Only a human controller can approve or decline it.

## Why it is safe

The workflow—not an LLM—decides what runs next. Source-trust rules resolve conflicts, every alert requires citations, the verifier fails closed, and policy gates refuse money movement, unsafe outreach, self-approval, and unverified bank changes. Corrections append new facts and selectively recheck only dependent records.

## Proof

The rules workflow passes all 9 planted scenarios with 100% cause accuracy, alert precision, recall, and citation rate. It executes zero prohibited actions and handles the changed-fact moment correctly. A one-strong-model baseline with the same 132 documents scores 8/9 and over-rechecks the moment. The live verifier blocks or refutes 8/8 planted bad drafts.

## Built with

Next.js, FastAPI, LangGraph, Pydantic, Anthropic Claude, Postgres-compatible rails, Neo4j mirror, and an append-only event model.

## What is next

The demo uses an in-memory session for fast, repeatable judging. The production path is authenticated controllers, durable Postgres checkpointing, and a deployed source-ingestion pipeline.
