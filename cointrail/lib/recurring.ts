/**
 * Recurring payments: one monthly-equivalent for every page (iteration 203 E2 M6).
 *
 * The Expenses Overview used to re-filter Ingest's active list with its own cutoffs and to call a
 * subscription "yearly" only when it had charged in exactly one month, ignoring Ingest's
 * `frequency`: a yearly plan charged in two different years (or a biennial one) counted at full
 * price every month and was labelled "/mo", so its "/month" headline disagreed with the Recurring
 * page. Both pages now use these helpers, and Ingest's list (already active-only) is not filtered.
 */

import { autoFormatCurrency } from "@/lib/formatters";
import { MONTH_SHORT } from "@/lib/constants";
import { parseLocalDate } from "@/lib/dates";

export interface RecurringLike {
  type: string;
  frequency?: string | null;
  avg_amount: number;
  months?: string[] | null;
}

function cadence(item: RecurringLike): "monthly" | "yearly" | "biennial" {
  if (item.type === "emi") return "monthly"; // installments are monthly
  if (item.frequency === "biennial") return "biennial";
  if (item.frequency === "yearly") return "yearly";
  if (item.frequency === "monthly") return "monthly";
  // An older Ingest without `frequency`: one month of charges is the only hint.
  return item.months?.length === 1 ? "yearly" : "monthly";
}

/** What the item costs per month on average. */
export function monthlyEquiv(item: RecurringLike): number {
  const c = cadence(item);
  if (c === "biennial") return item.avg_amount / 24;
  if (c === "yearly") return item.avg_amount / 12;
  return item.avg_amount;
}

/** "/mo", "/yr" or "/2yr" after the charged amount. */
export function frequencyLabel(item: RecurringLike): string {
  const c = cadence(item);
  return c === "biennial" ? "/2yr" : c === "yearly" ? "/yr" : "/mo";
}

/** "monthly", "yearly" or "every 2 years". */
export function cadenceLabel(item: RecurringLike): string {
  const c = cadence(item);
  return c === "biennial" ? "every 2 years" : c;
}

export function totalMonthly(items: RecurringLike[]): number {
  return items.reduce((s, i) => s + monthlyEquiv(i), 0);
}

/*
 * Cancelled series (iteration 203 E5). Ingest keeps a cancelled series out of its active list —
 * so every total above, on both pages, drops it — and reports it under `cancelled`, with any
 * charge dated after the cancellation.
 */

export interface ChargeAfterCancel {
  id: number;
  date: string;
  amount: number;
  card_last4: string | null;
}

/** Body for POST /api/recurring/cancellations. */
export function cancelPayload(
  item: { series_key: string; merchant: string; display_name?: string | null },
  cancelledOn: string
) {
  return {
    series_key: item.series_key,
    cancelled_on: cancelledOn,
    display_name: item.display_name || item.merchant,
  };
}

/** A real `YYYY-MM-DD` calendar date. */
export function isValidDay(s: string): boolean {
  return parseLocalDate(s) !== null && /^\d{4}-\d{2}-\d{2}$/.test(s);
}

function dayLabel(iso: string): string {
  const d = parseLocalDate(iso);
  return d ? `${d.getDate()} ${MONTH_SHORT[d.getMonth()]} ${d.getFullYear()}` : iso;
}

/**
 * What a series last cost (Ingest `last_amount`, iteration 203 E5 round 2): a cancelled card shows
 * this, not the average over older, cheaper charges. Falls back to the average from an older Ingest.
 */
export function lastChargeAmount(item: {
  last_amount?: number | null;
  avg_amount?: number | null;
}): number | null {
  if (typeof item.last_amount === "number") return item.last_amount;
  if (typeof item.avg_amount === "number") return item.avg_amount;
  return null;
}

/** The warning a cancelled series carries when it charged again, or null. */
export function chargedAfterCancelText(item: {
  charged_after_cancel?: ChargeAfterCancel[] | null;
}): string | null {
  const charges = item.charged_after_cancel ?? [];
  if (charges.length === 0) return null;
  if (charges.length === 1) {
    const c = charges[0];
    return `Charged ${autoFormatCurrency(c.amount)} on ${dayLabel(c.date)} after you cancelled it`;
  }
  const total = charges.reduce((s, c) => s + c.amount, 0);
  return `Charged ${charges.length} times after you cancelled it (${autoFormatCurrency(total)})`;
}
