# adr-002 — In-process SQLite task queue over Celery/Redis for the hourly pipeline

- **Status:** accepted · **Date:** 2026-04-01 · **Tags:** curated, cat:system

## Context
The hourly pipeline (email_sync -> pdf_process -> sms_process -> cross_match -> enrichment -> relink) needs ordered, retryable background execution. Celery + Redis would add two services to operate for a single-user tool.

## Decision
Use a `task_queue` table in the existing SQLite DB with a background runner thread (5s poll, 3 retries, orphaned-task recovery on restart, 30-min stuck-task sweep), driven by an APScheduler hourly job. No Redis/Celery.

## Consequences
- Zero extra infrastructure; the queue is queryable via `/tasks`.
- Survives restarts (orphan recovery) and self-heals stuck tasks.
- Bounded to single-process throughput - acceptable at personal scale.
- Recorded retroactively in the 2026-06-30 deep-dive.
