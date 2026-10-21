# adr-004 — Food analytics moved to cointrail; ingest stays a pure data hub

- **Status:** accepted · **Date:** 2026-04-01 · **Tags:** curated, cat:product

## Context
An early food-orders analytics module lived in ingest. Mixing a presentation/analytics concern into the data hub blurred its responsibility.

## Decision
Remove the food-analytics module from ingest; ingest exposes raw receipt/enrichment data via `/email/list` and the content API, and cointrail builds food analytics locally from it. Ingest stays a pure ingest/clean/store/serve hub.

## Consequences
- Clear separation: ingest = data, cointrail = presentation.
- cointrail's food helpers consume `/email/list` rather than a bespoke ingest endpoint.
- Recorded retroactively in the 2026-06-30 deep-dive.
