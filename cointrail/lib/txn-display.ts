/**
 * How a transaction amount is written in lists (iteration 203 E2 M3 / M4).
 *
 * Ingest returns `signed_amount`: positive for spend, negative for a refund / reversal / cashback
 * netted against it. The day panel and the category lists printed the unsigned `amount`, so a
 * refund looked like spend and the rows did not add up to the header. Credits are written with a
 * "+" (and shown green by the row component).
 */

import { autoFormatCurrency } from "@/lib/formatters";

export interface AmountParts {
  text: string;
  isCredit: boolean;
}

/** `signed` < 0 is money back. `debitSign` writes debits as "-₹..." (the card page's style). */
export function signedAmountParts(signed: number, debitSign = false): AmountParts {
  const isCredit = signed < 0;
  const abs = autoFormatCurrency(Math.abs(signed));
  return { text: isCredit ? `+${abs}` : debitSign ? `-${abs}` : abs, isCredit };
}

/** Signed amount of a row that may come from an older Ingest without `signed_amount`. */
export function signedOf(t: { amount: number; signed_amount?: number | null; txn_type?: string }) {
  if (typeof t.signed_amount === "number") return t.signed_amount;
  return t.txn_type === "credit" ? -Math.abs(t.amount) : t.amount;
}
