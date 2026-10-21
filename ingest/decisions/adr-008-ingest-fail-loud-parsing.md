# adr-008 — Fail loud: quarantine unreadable money messages; a golden corpus guards every parser change

- **Status:** accepted · **Date:** 2026-10-20 · **Tags:** curated, cat:reliability · **Iteration:** 198 (HP1, HP2, HP6)

## Context
An SMS that no pattern matched was marked processed and forgotten. Silent-drop rates were HDFC 2.5%, HSBC 11%, SBI 16%, ICICI 23%, Axis 27% — and 86% for Axis in October 2026 after it reworded its alert, with no alarm. `/health` only watched freshness, and Ingest had no unit tests.

## Decision
- Every financial message ends in exactly one state: transaction, known noise, bill payment / statement, or **quarantined** (`parse_quarantine`, grouped by template, surfaced in `/health` and a daily digest). Nothing with an amount and a money verb is silently dropped.
- A golden corpus of real (anonymised) message wordings with expected outputs runs in CI; a parser change that alters an expected result fails the build.
- `/health` reports correctness signals (quarantine size, per-bank coverage, app-vs-bank due-date gaps), not only freshness.

## Consequences
A bank changing its wording becomes a one-pattern fix on the day it happens, and retrying the quarantine recovers the held messages.
