/**
 * Card page wording that has to agree with the data (iteration 203 E2, verifier round 2 polish).
 * The page renders these; the logic lives here so it is tested without a DOM.
 */

import { autoFormatCurrency } from "@/lib/formatters";

/**
 * The other cards billed together with this one. On an add-on's page the other card is the
 * primary, so it is not called an "add-on" — only "same bill".
 */
export function sameBillNote(otherLast4s: string[]): string | null {
  if (otherLast4s.length === 0) return null;
  const label = otherLast4s.length === 1 ? "card" : "cards";
  return `incl. ${label} ${otherLast4s.join(", ")} (same bill)`;
}

interface BreakdownRow {
  category: string;
  total: number;
  count: number;
}

/**
 * Ingest nets refunds inside their category, so a category whose refunds outweigh its purchases in
 * the window comes back negative. That is not negative spending: it is money back, shown apart.
 */
export function splitCategoryBreakdown<T extends BreakdownRow>(
  rows: T[]
): { spend: T[]; refundsOnly: { category: string; refunded: number; count: number }[] } {
  return {
    spend: rows.filter((r) => r.total > 0),
    refundsOnly: rows
      .filter((r) => r.total < 0)
      .map((r) => ({ category: r.category, refunded: Math.abs(r.total), count: r.count })),
  };
}

/**
 * The note beside a net-refund category. The breakdown row nets purchases and refunds, so it cannot
 * tell "refunds only" from "refunds bigger than purchases"; the note claims only what is true of both.
 */
export function netRefundNote(): string {
  return "(refunds exceeded purchases this period)";
}

interface UnbilledBlock {
  total: number;
  count: number;
  credits?: { total: number; count: number } | null;
}

/**
 * The Unbilled tile for every card on the bill. Ingest counts spends and refunds apart; the
 * Unbilled list shows both, so the tile names both and `rows` equals the list's length.
 */
export function unbilledSummary(cards: { unbilled?: UnbilledBlock | null }[]): {
  total: number;
  count: number;
  credits: { total: number; count: number };
  rows: number;
  text: string;
} {
  const sum = cards.reduce(
    (s, c) => ({
      total: s.total + (c.unbilled?.total || 0),
      count: s.count + (c.unbilled?.count || 0),
      creditTotal: s.creditTotal + (c.unbilled?.credits?.total || 0),
      creditCount: s.creditCount + (c.unbilled?.credits?.count || 0),
    }),
    { total: 0, count: 0, creditTotal: 0, creditCount: 0 }
  );
  const text =
    sum.creditCount > 0
      ? `${sum.count} spend${sum.count === 1 ? "" : "s"} · ${sum.creditCount} refund${
          sum.creditCount === 1 ? "" : "s"
        } (+${autoFormatCurrency(sum.creditTotal)})`
      : `${sum.count} transaction${sum.count === 1 ? "" : "s"}`;
  return {
    total: sum.total,
    count: sum.count,
    credits: { total: sum.creditTotal, count: sum.creditCount },
    rows: sum.count + sum.creditCount,
    text,
  };
}
