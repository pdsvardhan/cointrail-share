/**
 * Card detail page periods (iteration 203 E2 M7 / C4).
 *
 * - "Current Statement" asks Ingest for `period=unbilled`, the same set the Unbilled tile counts
 *   (after the latest closed statement, plus the cycle's last days the statement did not bill).
 *   It used to send `from=<billing_end>` inclusive, so close-day rows sat in both statements.
 * - Both statement options are offered only when a statement has been captured; before, they
 *   silently became calendar months.
 * - "Last 3 / 6 Months" mean today minus 3 / 6 months, not the 1st of the month 3 / 6 back
 *   (on 20 Oct, "Last 3 Months" started on 1 Jul: 3.6 months).
 */

import { parseLocalDate, toLocalISODate } from "@/lib/dates";

export type CardPeriodKey = "current_stmt" | "last_stmt" | "3m" | "6m" | "all";

export interface StatementWindow {
  billing_start?: string | null;
  billing_end?: string | null;
}

const ALL_PERIODS: { key: CardPeriodKey; label: string; needsStatement: boolean }[] = [
  { key: "current_stmt", label: "Current Statement", needsStatement: true },
  { key: "last_stmt", label: "Last Statement", needsStatement: true },
  { key: "3m", label: "Last 3 Months", needsStatement: false },
  { key: "6m", label: "Last 6 Months", needsStatement: false },
  { key: "all", label: "All Time", needsStatement: false },
];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function hasStatement(stmt: StatementWindow | null | undefined): boolean {
  return !!(stmt?.billing_start && stmt?.billing_end);
}

export function cardPeriodOptions(stmt: StatementWindow | null | undefined) {
  const ok = hasStatement(stmt);
  return ALL_PERIODS.filter((p) => ok || !p.needsStatement).map(({ key, label }) => ({
    key,
    label,
  }));
}

export function defaultCardPeriod(stmt: StatementWindow | null | undefined): CardPeriodKey {
  return hasStatement(stmt) ? "current_stmt" : "3m";
}

/** `today` minus `n` calendar months, clamped to the month's length (31 May - 3 = 28/29 Feb). */
export function monthsBefore(today: Date, n: number): Date {
  const first = new Date(today.getFullYear(), today.getMonth() - n, 1);
  const last = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
  return new Date(first.getFullYear(), first.getMonth(), Math.min(today.getDate(), last));
}

/** Query string for /api/cards/{id}/transactions (without `limit`). */
export function cardTransactionsQuery(
  key: CardPeriodKey,
  stmt: StatementWindow | null | undefined,
  today: Date = new Date()
): string {
  const to = toLocalISODate(today);
  switch (key) {
    case "current_stmt":
      return "period=unbilled";
    case "last_stmt":
      return hasStatement(stmt) ? `from=${stmt!.billing_start}&to=${stmt!.billing_end}` : "";
    case "3m":
      return `from=${toLocalISODate(monthsBefore(today, 3))}&to=${to}`;
    case "6m":
      return `from=${toLocalISODate(monthsBefore(today, 6))}&to=${to}`;
    case "all":
      return "";
  }
}

/** The dropdown's label; "Unbilled (since 1 Oct)" once the statement close is known. */
export function cardPeriodLabel(key: CardPeriodKey, unbilledSince?: string | null): string {
  if (key === "current_stmt") {
    const d = parseLocalDate(unbilledSince);
    return d ? `Unbilled (since ${d.getDate()} ${MONTHS[d.getMonth()]})` : "Unbilled";
  }
  return ALL_PERIODS.find((p) => p.key === key)!.label;
}
