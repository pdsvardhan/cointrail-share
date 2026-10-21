/**
 * Expenses overview headline (iter-18 H5b / M13, brief contract 4).
 *
 * Ingest /analytics/overview now returns `total` as everyday spend net of refunds, plus
 * `refunds`, `outflows.{sent_to_people, investments}` (money that left the account but is
 * not consumption, previously 40-80% of the "spend" headline), `days_elapsed`,
 * `daily_avg` over calendar days, and `prev_same_period_total` (the same number of days
 * of the previous month). The old comparison set 18 days of October against all of
 * September. Every field below is optional so an older Ingest still renders.
 */

export interface OutflowBucket {
  total: number;
  count: number;
}

export interface OverviewFields {
  total: number;
  txn_count: number;
  daily_avg: number;
  prev_month_total?: number | null;
  gross_spend?: number | null;
  refunds?: number | null;
  fees_interest?: number | null;
  outflows?: {
    sent_to_people?: Partial<OutflowBucket> | null;
    investments?: Partial<OutflowBucket> | null;
    /** Loan repayments (e.g. a standing instruction to a loan account) — debt service, not spend. */
    loans?: Partial<OutflowBucket> | null;
  } | null;
  days_elapsed?: number | null;
  prev_same_period_total?: number | null;
  /**
   * What `prev_same_period_total` covers (iteration 203 E1): the same days of last month, the whole
   * previous month, or — for a 3M / 6M / 1Y / All range — the window of equal length just before it.
   */
  comparison_basis?: "same_days_last_month" | "previous_month" | "previous_window" | null;
  comparison_window?: { from: string; to: string; days: number } | null;
}

export interface OverviewHeadline {
  spending: number;
  /** Refund / reversal / cashback credits already netted out of `spending` (0 if none). */
  refunds: number;
  /** e.g. "+12.3% vs same days last month". */
  comparison: string;
  dailyAvg: number;
  dailyAvgNote: string;
  /** Null when Ingest does not report the bucket (older Ingest). */
  sentToPeople: OutflowBucket | null;
  investments: OutflowBucket | null;
  /** Null when Ingest does not report loans, or reports none this period. */
  loans: OutflowBucket | null;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function bucket(b: Partial<OutflowBucket> | null | undefined): OutflowBucket | null {
  const total = num(b?.total);
  if (total === null) return null;
  return { total, count: num(b?.count) ?? 0 };
}

/** Loans are rare: show the tile only in a period that has one. */
function nonEmpty(b: OutflowBucket | null): OutflowBucket | null {
  return b && b.count > 0 ? b : null;
}

function basisLabel(o: OverviewFields, sameDays: number | null): string {
  if (o.comparison_basis === "previous_window") {
    const days = num(o.comparison_window?.days);
    return days !== null ? `previous ${days} days` : "previous period";
  }
  if (o.comparison_basis === "previous_month") return "last month";
  return sameDays !== null ? "same days last month" : "last month";
}

/** Change against the previous period, preferring the same-days comparison. */
export function comparisonLabel(o: OverviewFields): string {
  const sameDays = num(o.prev_same_period_total);
  const prev = sameDays ?? num(o.prev_month_total) ?? 0;
  const basis = basisLabel(o, sameDays);
  if (prev <= 0) return `No spend in the ${basis} to compare`;
  const pct = ((o.total - prev) / prev) * 100;
  if (Math.abs(pct) < 0.05) return `Same as the ${basis}`;
  return `${pct > 0 ? "+" : ""}${pct.toFixed(1)}% vs ${basis}`;
}

export function overviewHeadline(o: OverviewFields): OverviewHeadline {
  const refunds = Math.max(num(o.refunds) ?? 0, 0);
  const days = num(o.days_elapsed);
  const txns = `${o.txn_count} transaction${o.txn_count !== 1 ? "s" : ""}`;
  return {
    spending: o.total,
    refunds,
    comparison: comparisonLabel(o),
    dailyAvg: o.daily_avg,
    dailyAvgNote:
      days !== null && days > 0 ? `${txns} over ${days} day${days !== 1 ? "s" : ""}` : txns,
    sentToPeople: bucket(o.outflows?.sent_to_people),
    investments: bucket(o.outflows?.investments),
    loans: nonEmpty(bucket(o.outflows?.loans)),
  };
}
