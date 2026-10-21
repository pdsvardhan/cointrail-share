# adr-005 — Cross-source dedup requires merchant corroboration (v2) to avoid false merges

- **Status:** accepted · **Date:** 2026-06-04 · **Tags:** curated, cat:reliability

## Context
A transaction can arrive from multiple sources (an SMS alert and the later statement line). Naive amount+date matching over-merged distinct transactions (same amount, same day, different merchants), under-counting spend.

## Decision
Cross-matcher v2 matches an SMS `source='cc'` transaction to a statement line on amount + (+/-1 day) + same/absent card, but ONLY when the merchant corroborates; otherwise they stay separate. The SMS row is flagged `is_duplicate` and the statement "winner" is enriched. (iter-13, 2026-06-04: recall + precedence-bug fix.)

## Consequences
- Eliminates false duplicates while still collapsing true ones.
- Dedup correctness is asserted by the data-integrity script.
- Recorded as an ADR in the 2026-06-30 deep-dive (the fix itself shipped 2026-06-04).
