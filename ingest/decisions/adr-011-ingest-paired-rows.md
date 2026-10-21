# adr-011 — Money that never left the owner is not an outflow: pairing rows that belong together

- **Status:** accepted · **Date:** 2026-10-20 · **Tags:** curated, cat:product · **Iteration:** 198 (H5b / M13)

## Context
Merging (adr-006) links copies of one money movement. Some different movements change each other's meaning: HSBC prints a move between the owner's own accounts as a bare "debited ... as per your request" on one account and "credited ... as per the transaction" on the other, naming neither; a card bill auto-debited from an account reads "as DEBIT"; a failed UPI payment comes back as a "reversal" credit that names only the reference. Read row by row, Rs 15 lakh of own-account moves and Rs 6,154 of card bills were "Sent to people", and reversals never offset the payments they undid.

## Decision
`modules/pairing.py` runs after every merge sweep:
- **own-transfer** — a bare debit on one own account and the same-amount bare credit on another own account that day or the next → both Self Transfer (excluded).
- **returned** — a bare debit and the same-amount bare credit on the same account the same day (the debit bounced and came straight back) → the credit becomes the debit's reversal; together they net to nothing.
- **card-autopay** — a bare account debit equal to a payment received on an active card within 3 days → Finance / bill_payment (excluded).
- **reversal** — a reversal credit with the same bank reference as a debit takes that debit's category and nets in its group (spend, or the outflow group of a failed transfer); one reversal per debit.
Only bare rows (the bank named no counterparty: HSBC "as DEBIT" / "as per your request", SBI core banking's "account transaction" e-mail) are paired as transfers or autopays; a returned or reversed credit follows the current label of its debit, including one the owner set by hand; pairs are 1:1, an ambiguous match pairs nothing, rows labelled by hand are never touched, and paired rows carry confidence `paired`, which recategorise leaves alone.

## Consequences
- "Sent to people" shows money that went to other people; the owner's own moves and card bills drop out.
- A bare debit with no partner (HSBC's Rs 45,000 on the 10th, when it did not bounce) stays a transfer until the owner says what it is.
