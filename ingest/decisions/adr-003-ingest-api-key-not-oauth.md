# adr-003 — API-key (X-API-Key) auth, not OAuth, for a single-user internal service

- **Status:** accepted · **Date:** 2026-04-01 · **Tags:** curated, cat:security

## Context
Ingest is single-user and internal-only (projects-net + LAN). Full OAuth/session auth would be overkill for one consumer (cointrail) on a private network.

## Decision
Gate every endpoint (except `/health`, `/docs`) with a static `INGEST_API_KEY` checked via `X-API-Key` using a timing-safe compare (`secrets.compare_digest`). CORS is locked to cointrail origins. If the key is unset the middleware is skipped and logs an "unauthenticated" warning. Network isolation is the outer boundary.

## Consequences
- Simple, fast, no token lifecycle to manage.
- Security depends on the key staying secret AND on network isolation - the service must NOT be publicly exposed without more (see the non-goal).
- Recorded retroactively in the 2026-06-30 deep-dive.
