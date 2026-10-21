# adr-001 — Pluggable DataSource layer + content types, not hardcoded per-source ingestion

- **Status:** accepted · **Date:** 2026-04-02 · **Tags:** curated, cat:system

## Context
Ingest pulls from three very different sources (SMS, Gmail, PDF statements) and is meant to grow into a general data hub (future: chat-bot/STT, notification forwarding). Hardcoding each source into the pipeline would not scale.

## Decision
Define a `DataSource` ABC plus a `ContentType` enum and register SMS/Email/PDF sources in a registry (`sources/`). A unified `/content/*` API queries across sources by content type. New sources plug into the same contract.

## Consequences
- New ingestion sources are additive, not invasive.
- A single `/content/query` seam serves heterogeneous data to consumers.
- Recorded retroactively in the 2026-06-30 Ottomate deep-dive (ingest had zero in-repo ADRs; this captures the decision made during the 2026-04 architecture rebuild).
