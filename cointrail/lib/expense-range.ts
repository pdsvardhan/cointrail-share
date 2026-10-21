/**
 * Expenses page date windows (iteration 203 E1 / E2).
 *
 * The 3M / 6M / 1Y / All pills used to hide the month picker and nothing else: every request
 * still carried `period=YYYY-MM`. A selection is now turned into an explicit window — IST
 * calendar dates, `from`..`to` inclusive, the same contract Ingest's analytics endpoints read —
 * and every tab builds its request from it with the URL helpers below, so no tab can forget it.
 *
 *   month : the picked month (or, before the first response, "let the server pick the latest
 *           month with data" — E2 M2)
 *   3M    : 1st of the month two months back .. today   (6M: five back, 1Y: eleven back)
 *   All   : the first transaction (Ingest `data_since`) .. today
 *
 * The heatmap and the trend charts follow the selection too (E2 C2): a range shows exactly the
 * range; a single month shows the 6 (heatmap) or 12 (trends) months ending with it.
 */

import { parseLocalDate, toLocalISODate } from "@/lib/dates";

export type RangeMode = "month" | "3M" | "6M" | "1Y" | "all";

export interface DateRange {
  from: string;
  to: string;
}

export interface Selection {
  mode: RangeMode;
  /** `YYYY-MM` in month mode; null until the server has said which month has the latest data. */
  period: string | null;
}

const RANGE_MONTHS: Record<"3M" | "6M" | "1Y", number> = { "3M": 3, "6M": 6, "1Y": 12 };
const HEATMAP_MONTHS = 6;
const TREND_MONTHS = 12;
/** Used for All only when Ingest has not reported where the data starts. */
export const FALLBACK_SINCE = "2020-01-01";
/** Up to this many days the spending chart shows days; longer windows show months (E1 AC3). */
export const DAILY_CHART_MAX_DAYS = 62;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function iso(d: Date): string {
  return toLocalISODate(d);
}

function minIso(a: string, b: string): string {
  return a < b ? a : b;
}

/** `YYYY-MM` of a local date. */
export function monthKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

/** First and last calendar day of `YYYY-MM`. */
export function monthBounds(period: string): DateRange {
  const [y, m] = period.split("-").map(Number);
  return { from: iso(new Date(y, m - 1, 1)), to: iso(new Date(y, m, 0)) };
}

/** The `months` calendar months ending with `period`, never past today. */
function monthsEndingWith(period: string, months: number, today: Date): DateRange {
  const [y, m] = period.split("-").map(Number);
  const end = monthBounds(period).to;
  return { from: iso(new Date(y, m - months, 1)), to: minIso(end, iso(today)) };
}

/** The window a range pill stands for, or null for month mode. */
export function rangeForMode(
  mode: RangeMode,
  today: Date,
  dataSince?: string | null
): DateRange | null {
  const to = iso(today);
  if (mode === "month") return null;
  if (mode === "all") {
    const since = parseLocalDate(dataSince) && dataSince! <= to ? dataSince!.slice(0, 10) : null;
    return { from: since ?? FALLBACK_SINCE, to };
  }
  const back = RANGE_MONTHS[mode] - 1;
  return { from: iso(new Date(today.getFullYear(), today.getMonth() - back, 1)), to };
}

/** The selection's own window (what the headline adds up), or null before the first month is known. */
export function selectionWindow(
  sel: Selection,
  today: Date,
  dataSince?: string | null
): DateRange | null {
  if (sel.mode !== "month") return rangeForMode(sel.mode, today, dataSince);
  return sel.period ? monthBounds(sel.period) : null;
}

/** Heatmap window: the range itself, or the 6 months ending with the picked month. */
export function heatmapWindow(
  sel: Selection,
  today: Date,
  dataSince?: string | null
): DateRange | null {
  if (sel.mode !== "month") return rangeForMode(sel.mode, today, dataSince);
  return sel.period ? monthsEndingWith(sel.period, HEATMAP_MONTHS, today) : null;
}

/** Trends window: the range itself, or the 12 months ending with the picked month. */
export function trendWindow(
  sel: Selection,
  today: Date,
  dataSince?: string | null
): DateRange | null {
  if (sel.mode !== "month") return rangeForMode(sel.mode, today, dataSince);
  return sel.period ? monthsEndingWith(sel.period, TREND_MONTHS, today) : null;
}

function rangeQuery(w: DateRange): string {
  return `from=${w.from}&to=${w.to}`;
}

/** Query string for the selection: `period=` in month mode, `from=&to=` for a range. */
export function selectionQuery(sel: Selection, today: Date, dataSince?: string | null): string {
  if (sel.mode === "month") return sel.period ? `period=${sel.period}` : "";
  return rangeQuery(rangeForMode(sel.mode, today, dataSince)!);
}

function withQuery(path: string, query: string): string {
  return query ? `${path}?${query}` : path;
}

// ── One URL builder per request the page makes, so every tab carries the selection ─────────

export function dashboardUrl(sel: Selection, today: Date, dataSince?: string | null): string {
  return withQuery("/api/expenses/dashboard", selectionQuery(sel, today, dataSince));
}

export function categoriesUrl(
  sel: Selection,
  today: Date,
  dataSince?: string | null,
  category?: string
): string {
  const parts = [selectionQuery(sel, today, dataSince)];
  if (category) parts.push(`category=${encodeURIComponent(category)}`);
  return withQuery("/api/expenses/dashboard/categories", parts.filter(Boolean).join("&"));
}

export function merchantDetailUrl(
  sel: Selection,
  today: Date,
  dataSince: string | null | undefined,
  merchant: string,
  category: string
): string {
  const w = selectionWindow(sel, today, dataSince);
  const parts = [
    `merchant=${encodeURIComponent(merchant)}`,
    `category=${encodeURIComponent(category)}`,
  ];
  if (w) parts.push(rangeQuery(w));
  return withQuery("/api/expenses/dashboard/merchant-detail", parts.join("&"));
}

export function allTransactionsUrl(
  sel: Selection,
  today: Date,
  dataSince: string | null | undefined,
  sort: "amount" | "date",
  offset: number,
  limit = 50
): string {
  const parts = [selectionQuery(sel, today, dataSince), `sort=${sort}`, `offset=${offset}`];
  parts.push(`limit=${limit}`);
  return withQuery("/api/expenses/dashboard/transactions", parts.filter(Boolean).join("&"));
}

export function foodOrdersUrl(sel: Selection, today: Date, dataSince?: string | null): string {
  const w = selectionWindow(sel, today, dataSince);
  return withQuery(
    "/api/food/orders",
    [w ? rangeQuery(w) : "", "limit=2000"].filter(Boolean).join("&")
  );
}

export function trendsUrl(sel: Selection, today: Date, dataSince?: string | null): string {
  const w = trendWindow(sel, today, dataSince);
  return withQuery("/api/expenses/dashboard/trends", w ? rangeQuery(w) : "");
}

export function heatmapUrl(sel: Selection, today: Date, dataSince?: string | null): string {
  const w = heatmapWindow(sel, today, dataSince);
  return withQuery("/api/expenses/dashboard/heatmap", w ? rangeQuery(w) : "");
}

// ── Labels ─────────────────────────────────────────────────────────────────────────────

export function monthLabel(period: string): string {
  const [y, m] = period.split("-").map(Number);
  return `${MONTHS[m - 1]} ${y}`;
}

/** "Oct 2026" for a calendar month (to date), else "1 Aug – 20 Oct 2026". */
export function windowLabel(w: DateRange): string {
  const a = parseLocalDate(w.from);
  const b = parseLocalDate(w.to);
  if (!a || !b) return `${w.from} – ${w.to}`;
  if (a.getDate() === 1 && monthKey(a) === monthKey(b)) return monthLabel(monthKey(a));
  const left =
    a.getFullYear() === b.getFullYear()
      ? `${a.getDate()} ${MONTHS[a.getMonth()]}`
      : `${a.getDate()} ${MONTHS[a.getMonth()]} ${a.getFullYear()}`;
  return `${left} – ${b.getDate()} ${MONTHS[b.getMonth()]} ${b.getFullYear()}`;
}

/** Whole calendar days in the window, both ends included. */
export function windowDays(w: DateRange): number {
  const a = parseLocalDate(w.from);
  const b = parseLocalDate(w.to);
  if (!a || !b) return 0;
  const ms =
    Date.UTC(b.getFullYear(), b.getMonth(), b.getDate()) -
    Date.UTC(a.getFullYear(), a.getMonth(), a.getDate());
  return Math.round(ms / 864e5) + 1;
}

/**
 * E2 M2: the page opens on the latest month with data; say so when that is not this month
 * (e.g. the SMS feed stopped). Null when the data is current.
 */
export function staleDataNote(dataUntil: string | null | undefined, today: Date): string | null {
  const d = parseLocalDate(dataUntil);
  if (!d) return null;
  if (monthKey(d) === monthKey(today)) return null;
  return `Latest data: ${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

/** The month picker's options: the last 12 months plus any month the page is showing. */
export function monthOptions(today: Date, extra: (string | null | undefined)[] = []): string[] {
  const out = new Set<string>();
  for (let i = 0; i < 12; i++)
    out.add(monthKey(new Date(today.getFullYear(), today.getMonth() - i, 1)));
  for (const e of extra) if (e && /^\d{4}-\d{2}$/.test(e)) out.add(e);
  return [...out].sort().reverse();
}

// ── Charts ─────────────────────────────────────────────────────────────────────────────

export interface SpendBar {
  /** `YYYY-MM-DD` for a day bar, `YYYY-MM` for a month bar. */
  key: string;
  kind: "day" | "month";
  label: string;
  total: number;
}

/**
 * E1 AC3: the spending chart over the window. Up to DAILY_CHART_MAX_DAYS days it is one bar per
 * day (labelled with the day, plus the month when the window spans months); longer windows get
 * one bar per calendar month, every month of the window present, so a multi-month range never
 * looks like a single month.
 */
export function spendBars(daily: { date: string; total: number }[], w: DateRange): SpendBar[] {
  const inside = daily.filter((d) => d.date >= w.from && d.date <= w.to);
  if (windowDays(w) <= DAILY_CHART_MAX_DAYS) {
    const oneMonth = w.from.slice(0, 7) === w.to.slice(0, 7);
    return inside.map((d) => {
      const day = parseLocalDate(d.date)!;
      return {
        key: d.date,
        kind: "day" as const,
        label: oneMonth ? String(day.getDate()) : `${day.getDate()} ${MONTHS[day.getMonth()]}`,
        total: d.total,
      };
    });
  }
  const totals = new Map<string, number>();
  for (const d of inside)
    totals.set(d.date.slice(0, 7), (totals.get(d.date.slice(0, 7)) ?? 0) + d.total);
  const bars: SpendBar[] = [];
  const start = parseLocalDate(w.from)!;
  const end = w.to.slice(0, 7);
  for (let i = 0; ; i++) {
    const k = monthKey(new Date(start.getFullYear(), start.getMonth() + i, 1));
    if (k > end) break;
    const [y, m] = k.split("-").map(Number);
    bars.push({
      key: k,
      kind: "month",
      label: `${MONTHS[m - 1]} ${String(y).slice(2)}`,
      total: Math.round((totals.get(k) ?? 0) * 100) / 100,
    });
  }
  return bars;
}

/** Ingest numbers weekdays the Python way, 0 = Monday (E2 B2: the chart read 0 as Sunday). */
const INGEST_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function ingestWeekdayLabel(dow: number): string {
  return INGEST_WEEKDAYS[dow] ?? "?";
}

/** E2 C5: the trend chart starts with the biggest categories, not the first 4 alphabetically. */
export function topCategoriesByTotal(
  trends: Record<string, Record<string, number>>,
  n = 4
): string[] {
  return Object.entries(trends)
    .map(([cat, months]) => [cat, Object.values(months).reduce((s, v) => s + v, 0)] as const)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, n)
    .map(([cat]) => cat);
}

/** E2 M5: a bar's width against the largest value in its set (it used the newest month's). */
export function barWidthPct(value: number, values: number[]): number {
  const max = Math.max(0, ...values);
  if (max <= 0 || value <= 0) return 0;
  return Math.min(100, (value / max) * 100);
}
