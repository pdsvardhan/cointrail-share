# adr-010 — Only an order confirmation may relabel a transaction

- **Status:** accepted · **Date:** 2026-10-20 · **Tags:** curated, cat:product · **Iteration:** 198 (M12)

## Context
Brand emails (Swiggy, Zomato, Uber, Amazon …) enrich a transaction with the restaurant, ride or product and re-file its category. The matcher took any email with a number in it and the closest debit within a tolerance and a multi-day window. A Rs 15,000 CRED bill payment became "Shopping" next to a Rs 14,500 Amazon order; marketing emails ("mornings starting at just Rs 39") relabelled an Airtel payment as Swiggy; "Payment Failed", surveys, refunds and Splitwise "X paid you" were matched to debits; Instamart grocery orders (sent from swiggy.in) were filed as Food; IGST fee lines were labelled with restaurants. 173 live rows (Rs 1.89 lakh) carried such links.

## Decision
- Only an **order / ride / booking / payment confirmation** is matched (subject patterns from the emails on file); promotions, surveys, refunds, cancellations and failed payments are kept as unlinked enrichments.
- The email's **own amount**, to the rupee, within **±1 IST day**, and exactly **one** candidate — otherwise nothing is linked. A debit another email already explains is not a candidate (one order per debit).
- An Uber receipt matches a debit that names Uber by date, but a payment to a person (the driver of a cash trip) only when it was made between 2 minutes before and 2 hours after the receipt. A driver paid a rounded-up amount (Rs 280 for a Rs 276.24 fare) is outside the email's own amount and stays unlinked — loosening that is the owner's call.
- Never a candidate: bill payments, money in, Self Transfer / Finance rows, fee / interest / EMI / ATM lines, estimated foreign amounts, and a debit whose text names another brand. Rows the owner labelled by hand are linked but keep their labels.
- Instamart orders are Groceries whatever address they come from; one enrichment row per email.
- Emails are matched oldest first (by when they were sent), so of two same-amount orders the first one explains the payment. An order email whose debit has not arrived yet is tried again every hour for 400 days (the statement line, an SMS the backfill recovers, or a bulk import can come weeks or months later). An order's link and labels follow the copy that stays live when a statement line later wins the merge.
- `rebuild_enrichment_links` re-makes every link once at deploy: rows labelled by an email get the categoriser's labels back (unless the owner labelled them), then every brand email — filed before or not — is matched again, oldest first, exactly as the hourly pass would. Ambiguous old links disappear, links on merged copies land on the live row, and emails the old matcher missed get their match.

## Consequences
- Fewer rows carry restaurant / product detail, but none carries a wrong one; a missed match only leaves the bank's own description.
- The same constants govern the hourly enrichment, the relink pass and the audit, so they cannot drift apart.
