# adr-007 — Units are first-class: currency, IST calendar, and what kind of money movement

- **Status:** accepted · **Date:** 2026-10-20 · **Tags:** curated, cat:system · **Iteration:** 198 (HP4, M7, M8, H8, H5)

## Context
The data model had no currency (USD 20 stored as Rs 20), no timezone (UTC containers turned 00:00–05:30 IST into the previous day or month), no year for year-less bank dates (PDF parsers hard-coded 2026), and only debit/credit for direction, so refunds, cashback, reversals, fees, interest, EMI principal and bill payments were indistinguishable and refunds were never netted.

## Decision
- `transactions.currency`, `original_amount`, `fx_estimated`: `amount` is always INR; a foreign spend keeps its original amount and is marked estimated until the statement line supplies the exact INR.
- Storage stays UTC (so SMS dedup across paths is unchanged); every calendar decision uses `util.timez` (IST), never the container clock.
- Year-less dates use `infer_year`: the year that puts the date at or before the reference date.
- `transactions.kind` records what happened (purchase, refund, reversal, cashback, fee, interest, emi_principal, emi_conversion, atm, transfer_in, debit, bill_payment). Category says where money went; kind says what happened. Spend = purchases + fees/interest − refunds/reversals/cashback, excluding transfers and EMI principal (the purchase was already counted).

## Consequences
Totals become net and honest; the January-2027 year rollover is safe; foreign spends are no longer under-valued ~90x.
