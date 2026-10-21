# adr-009 — The bank's numbers win: due dates, amounts due and payments come from the bank

- **Status:** accepted · **Date:** 2026-10-20 · **Tags:** curated, cat:product · **Iteration:** 198 (HP5, H1, H2, M1, M11, M15)

## Context
The card screen showed a due date computed from a hard-coded day (Axis "Nov 5" when the bank said 1 Nov), compared payments against gross spend (a fully paid HDFC bill showed "Overdue Rs 511"), only learned about payments from bank SMS (CRED-paid bills were invisible), and fell back to guesses (95% = paid, a 45-day lifetime ratio) when evidence was missing. CRED, which reads the banks' own figures, was right on every card.

## Decision
- `statements.due_date` / `min_due` store the bank's date and minimum from statement SMS and bank / CRED statement emails; the configured due day is only an estimate, labelled as such.
- Amount due = the bank's stated total; otherwise the net of parsed charges and credits — never gross spend.
- Payments come from bank SMS, bank payment emails and CRED payment confirmations, plus bank-side debits to CRED matched to stated totals.
- Paid means paid (within rounding). Missing evidence shows as "not confirmed", not as paid.

## Consequences
The app agrees with the bank on due dates and on what is owed; it says "not confirmed" instead of guessing.
