"use client";

import { Fragment, useState, useEffect, useMemo, useRef } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "motion/react";
import {
  Receipt,
  TrendingUp,
  TrendingDown,
  Minus,
  Loader2,
  Wallet,
  Activity,
  CalendarDays,
  ArrowUpRight,
  ArrowDownRight,
  ChevronRight,
  ChevronDown,
  BarChart3,
  PieChart as PieChartIcon,
  Flame,
  Repeat,
  Clock,
  ArrowRight,
  Hash,
  DollarSign,
  Utensils,
  ShoppingBag,
  Car,
  Zap,
  Home,
  Heart,
  Tv,
  Plane,
  GraduationCap,
  User,
  CreditCard,
  HelpCircle,
  Fuel,
  Smartphone,
  Package,
  Landmark,
  Shield,
  Gift,
  Globe,
  Coffee,
  Building2,
  Users,
  Send,
  ArrowLeftRight,
  RotateCcw,
  X,
} from "lucide-react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  CartesianGrid,
  LineChart,
  Line,
  Legend,
} from "recharts";
import { autoFormatCurrency } from "@/lib/formatters";
import {
  CATEGORY_COLORS,
  CATEGORY_ICONS,
  MONTH_SHORT,
  getCategoryColor,
  getCategoryIcon,
} from "@/lib/constants";
import { AnimatedCurrency, AnimatedNumber } from "@/components/shared/AnimatedCurrency";
import { GradientStatCard } from "@/components/shared/StatCard";
import { ErrorState } from "@/components/shared/ErrorState";
import { TxnRow } from "@/components/shared/TxnRow";
import { ChangeCategoryButton, loadCategoryChoices } from "@/components/shared/ChangeCategory";
import {
  outflowCards,
  outflowCategoryChoices,
  outflowGroupLabel,
  versionedKey,
  type OutflowCard,
} from "@/lib/category-edit";
import { DataVersionProvider, useDataVersion } from "@/components/shared/DataVersion";
import { PayeeRulesButton } from "@/components/expenses/PayeeRulesDialog";
import { parseLocalDate, toLocalISODate } from "@/lib/dates";
import { overviewHeadline, type OverviewFields } from "@/lib/expense-overview";
import {
  allTransactionsUrl,
  barWidthPct,
  categoriesUrl,
  dashboardUrl,
  foodOrdersUrl,
  heatmapUrl,
  heatmapWindow,
  ingestWeekdayLabel,
  merchantDetailUrl,
  monthLabel,
  monthOptions,
  selectionWindow,
  spendBars,
  staleDataNote,
  topCategoriesByTotal,
  trendsUrl,
  trendWindow,
  windowLabel,
  type DateRange,
  type RangeMode,
  type Selection,
} from "@/lib/expense-range";
import { frequencyLabel, cadenceLabel, totalMonthly } from "@/lib/recurring";
import { summarizeFood, type FoodSort } from "@/lib/food-summary";
import { signedOf } from "@/lib/txn-display";
import { errorMessage, getJson } from "@/lib/http";

/* ═══════════════════════════════════════════════════════════════════════════
   Types
   ═══════════════════════════════════════════════════════════════════════════ */

// Contract-4 fields (refunds, outflows, days_elapsed, prev_same_period_total, ...) come
// from OverviewFields and are optional, so an older Ingest still renders (iter-18 H5b/M13).
interface OverviewData extends OverviewFields {
  /** `YYYY-MM` in month mode; null for a range (iteration 203 E1). */
  period: string | null;
  range?: DateRange | null;
  /** First / last transaction date in the data (anchors "All"; the stale-data note, E2 M2). */
  data_since?: string | null;
  data_until?: string | null;
  total: number;
  txn_count: number;
  daily_avg: number;
  avg_txn: number;
  highest_day: { date: string; total: number } | null;
  top_category: { category: string; total: number; count: number } | null;
  prev_month_total: number;
  prev_month_txn_count: number;
  categories: { category: string; total: number; count: number }[];
  daily_spend: { date: string; total: number; count: number }[];
  top_transactions: {
    id: number;
    amount: number;
    merchant: string;
    category: string;
    date: string;
    source: string;
    raw_description: string;
  }[];
}

interface RecurringItem {
  merchant: string;
  category: string;
  type: string;
  frequency?: "monthly" | "yearly" | "biennial" | null;
  avg_amount: number;
  occurrences: number;
  months: string[];
  last_date: string;
  next_expected: string | null;
  total_paid: number;
  logo_domain: string | null;
  card_last4: string | null;
  payments_done: number | null;
  total_installments: number | null;
  product_name: string | null;
  display_name: string | null;
}

interface CategoryDetail {
  category: string;
  total: number;
  count: number;
  merchants: { name: string; total: number; count: number }[];
}

interface TrendsData {
  monthly: { month: string; total: number; count: number }[];
  category_trends: Record<string, Record<string, number>>;
  categories: string[];
  weekday_distribution: {
    dow: number;
    avg_spend: number;
    total_spend: number;
    day_count: number;
  }[];
  top_merchants: { name: string; total: number; count: number }[];
  merchant_trends: Record<string, Record<string, number>>;
}

interface HeatmapDay {
  date: string;
  total: number;
  count: number;
}

type TabId = "overview" | "categories" | "food" | "trends";

/* ═══════════════════════════════════════════════════════════════════════════
   Constants
   ═══════════════════════════════════════════════════════════════════════════ */
const CHART_COLORS = [
  "#3b82f6",
  "#8b5cf6",
  "#06b6d4",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#ec4899",
  "#14b8a6",
  "#f97316",
  "#84cc16",
];
// Heatmap rows follow the browser's Date.getDay() (0 = Sunday). Ingest's weekday numbers are
// Python's (0 = Monday) and go through ingestWeekdayLabel (iteration 203 E2 B2).
const HEATMAP_ROW_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function getMerchantLogo(domain: string | null): string | null {
  if (!domain) return null;
  return `https://www.google.com/s2/favicons?domain=${domain}&sz=32`;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Custom Tooltip
   ═══════════════════════════════════════════════════════════════════════════ */

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--card)] px-3 py-2 shadow-2xl">
      <div className="mb-1 font-mono text-[11px] text-[var(--text-muted)]">{label}</div>
      {payload.map((p: any, i: number) => (
        <div
          key={i}
          className="font-mono text-[12px] font-semibold"
          style={{ color: p.color || p.fill }}
        >
          {p.name}: {autoFormatCurrency(p.value)}
        </div>
      ))}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Donut Chart Label
   ═══════════════════════════════════════════════════════════════════════════ */

function DonutCenter({ total, label }: { total: number; label?: string }) {
  return (
    <text x="50%" y="50%" textAnchor="middle" dominantBaseline="central">
      <tspan x="50%" dy="-8" className="fill-[var(--text-muted)]" fontSize={11} fontWeight={500}>
        {label || "Spending"}
      </tspan>
      <tspan
        x="50%"
        dy="20"
        className="fill-[var(--foreground)]"
        fontSize={16}
        fontWeight={700}
        style={{ fontVariantNumeric: "tabular-nums", fontFamily: "Outfit, sans-serif" }}
      >
        {autoFormatCurrency(total)}
      </tspan>
    </text>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Outside-spend item — refunds / transfers / investments row on the Overview
   ═══════════════════════════════════════════════════════════════════════════ */

function OutsideSpendItem({
  icon: Icon,
  label,
  value,
  note,
  onOpen,
}: {
  icon: React.ElementType;
  label: string;
  value: number;
  note: string;
  /** E3: open these payments (Categories → Outside Spending) to re-file one. */
  onOpen?: () => void;
}) {
  const body = (
    <>
      <div className="flex-shrink-0 rounded-md bg-[var(--stat-bg)] p-1.5">
        <Icon className="h-3.5 w-3.5 text-[var(--text-muted)]" />
      </div>
      <div className="min-w-0">
        <div className="text-[10px] font-bold tracking-wider text-[var(--text-muted)] uppercase">
          {label}
        </div>
        <div className="flex items-baseline gap-1.5">
          <span className="font-mono text-sm font-bold text-[var(--foreground)]">
            {autoFormatCurrency(value)}
          </span>
          <span className="text-[10px] text-[var(--text-muted)]">{note}</span>
        </div>
      </div>
    </>
  );
  if (onOpen) {
    return (
      <button
        type="button"
        onClick={onOpen}
        title={`See the ${label.toLowerCase()} payments`}
        className="-mx-2 flex items-center gap-2.5 rounded-lg px-2 py-1 text-left transition-colors hover:bg-[var(--card-hover)]"
      >
        {body}
      </button>
    );
  }
  return <div className="flex items-center gap-2.5">{body}</div>;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Calendar Heatmap (quantile-based colors + sidebar stats)
   ═══════════════════════════════════════════════════════════════════════════ */

interface DayTransaction {
  id: number;
  amount: number;
  /** Negative for a refund / cashback netted against the day's spend (E2 M3). */
  signed_amount?: number;
  txn_type?: string;
  merchant: string;
  category: string;
  raw_description: string;
  source: string;
  card_last4: string | null;
}

/** A transaction in the period / category lists (Ingest analytics `_txn_view`). */
interface ListTransaction {
  id: number;
  amount: number;
  signed_amount?: number;
  date: string;
  merchant: string | null;
  category: string | null;
  raw_description: string | null;
  source?: string;
  card_last4?: string | null;
  txn_type?: string;
  kind?: string;
  enrichment?: any;
}

function CategoryBadgeIcon({ category }: { category: string | null }) {
  const cat = category || "Uncategorized";
  const Icon = getCategoryIcon(cat);
  return (
    <div
      className="flex-shrink-0 rounded-md p-1.5"
      style={{ backgroundColor: getCategoryColor(cat) + "18" }}
    >
      <Icon className="h-3.5 w-3.5" style={{ color: getCategoryColor(cat) }} />
    </div>
  );
}

function shortDate(iso: string, withYear = false): string {
  const d = parseLocalDate(iso);
  if (!d) return iso;
  return d.toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    ...(withYear ? { year: "numeric" } : {}),
  });
}

function CalendarHeatmap({
  data,
  span,
  onDayClick,
  selectedDate,
}: {
  data: HeatmapDay[];
  /** The days drawn: the page's range, or the 6 months ending with its month (E2 C2). */
  span: DateRange;
  onDayClick?: (date: string) => void;
  selectedDate?: string | null;
}) {
  const dataMap = useMemo(() => {
    const m = new Map<string, number>();
    data.forEach((d) => m.set(d.date, d.total));
    return m;
  }, [data]);

  const { weeks, months } = useMemo(() => {
    const end = parseLocalDate(span.to) ?? new Date();
    const start = parseLocalDate(span.from) ?? new Date(end.getFullYear(), end.getMonth() - 5, 1);
    while (start.getDay() !== 0) start.setDate(start.getDate() - 1);

    const weeks: { date: Date; total: number }[][] = [];
    let currentWeek: { date: Date; total: number }[] = [];
    const months: { label: string; col: number }[] = [];
    let lastMonth = -1;
    let col = 0;

    const cursor = new Date(start);
    while (cursor <= end) {
      if (cursor.getDay() === 0 && currentWeek.length > 0) {
        weeks.push(currentWeek);
        currentWeek = [];
        col++;
      }
      // Local calendar date: toISOString() is UTC, so between 00:00 and 05:30 IST every
      // cell showed the previous day's spend (iter-18 M9).
      const dateStr = toLocalISODate(cursor);
      currentWeek.push({ date: new Date(cursor), total: dataMap.get(dateStr) || 0 });

      // Label a month only from its first day inside the window (the grid starts on the Sunday
      // before it, so a 1 Aug range would otherwise show a "Jul" column).
      if (cursor.getMonth() !== lastMonth && dateStr >= span.from) {
        months.push({ label: MONTH_SHORT[cursor.getMonth()], col });
        lastMonth = cursor.getMonth();
      }
      cursor.setDate(cursor.getDate() + 1);
    }
    if (currentWeek.length > 0) weeks.push(currentWeek);

    return { weeks, months };
  }, [dataMap, span.from, span.to]);

  // Quantile-based color distribution
  const quantiles = useMemo(() => {
    const vals = data
      .filter((d) => d.total > 0)
      .map((d) => d.total)
      .sort((a, b) => a - b);
    if (vals.length === 0) return [0, 0, 0, 0];
    return [
      vals[Math.floor(vals.length * 0.25)] || 0,
      vals[Math.floor(vals.length * 0.5)] || 0,
      vals[Math.floor(vals.length * 0.75)] || 0,
      vals[vals.length - 1] || 0,
    ];
  }, [data]);

  const getColor = (val: number) => {
    if (val === 0) return "var(--border-subtle)";
    if (val <= quantiles[0]) return "#134e4a";
    if (val <= quantiles[1]) return "#0d9488";
    if (val <= quantiles[2]) return "#2dd4bf";
    return "#5eead4";
  };

  const [tooltip, setTooltip] = useState<{
    x: number;
    y: number;
    date: string;
    total: number;
  } | null>(null);
  const cellSize = 13;
  const gap = 3;

  return (
    <div className="relative">
      <div className="overflow-x-auto pb-2">
        <svg
          viewBox={`0 0 ${weeks.length * (cellSize + gap) + 30} ${7 * (cellSize + gap) + 24}`}
          width="100%"
          preserveAspectRatio="xMidYMin meet"
          className="block h-auto w-full"
        >
          {months.map((m, i) => (
            <text
              key={i}
              x={m.col * (cellSize + gap) + 30}
              y={10}
              className="fill-[var(--text-muted)]"
              fontSize={10}
              fontWeight={500}
            >
              {m.label}
            </text>
          ))}
          {[1, 3, 5].map((d) => (
            <text
              key={d}
              x={12}
              y={d * (cellSize + gap) + 24 + cellSize / 2}
              textAnchor="end"
              className="fill-[var(--text-muted)]"
              fontSize={9}
              dominantBaseline="central"
            >
              {HEATMAP_ROW_LABELS[d]}
            </text>
          ))}
          {weeks.map((week, wi) =>
            week.map((day, di) => {
              const dateStr = toLocalISODate(day.date);
              const inWindow = dateStr >= span.from && dateStr <= span.to;
              const clickable = inWindow && day.total !== 0 && !!onDayClick;
              return (
                <rect
                  key={`${wi}-${di}`}
                  x={wi * (cellSize + gap) + 30}
                  y={di * (cellSize + gap) + 18}
                  width={cellSize}
                  height={cellSize}
                  rx={2}
                  fill={inWindow ? getColor(day.total) : "transparent"}
                  stroke={selectedDate === dateStr ? "#f59e0b" : "none"}
                  strokeWidth={selectedDate === dateStr ? 2 : 0}
                  className={
                    clickable
                      ? "cursor-pointer transition-all duration-150 hover:brightness-125"
                      : undefined
                  }
                  data-date={dateStr}
                  onClick={() => {
                    if (clickable) onDayClick!(dateStr);
                  }}
                  onMouseEnter={(e) => {
                    const rect = e.currentTarget.getBoundingClientRect();
                    setTooltip({
                      x: rect.left + rect.width / 2,
                      y: rect.top,
                      date: day.date.toLocaleDateString("en-IN", {
                        weekday: "short",
                        day: "numeric",
                        month: "short",
                      }),
                      total: day.total,
                    });
                  }}
                  onMouseLeave={() => setTooltip(null)}
                />
              );
            })
          )}
        </svg>
      </div>
      {tooltip && (
        <div
          className="pointer-events-none fixed z-50 rounded-lg border border-[var(--border)] bg-[var(--card)] px-3 py-2 text-xs shadow-2xl"
          style={{ left: tooltip.x, top: tooltip.y - 48, transform: "translateX(-50%)" }}
        >
          <div className="text-[var(--text-muted)]">{tooltip.date}</div>
          <div className="font-mono font-bold">
            {tooltip.total > 0 ? autoFormatCurrency(tooltip.total) : "No spend"}
          </div>
        </div>
      )}
      <div className="mt-2 flex items-center justify-end gap-2">
        <span className="text-[10px] text-[var(--text-muted)]">Less</span>
        {["var(--border-subtle)", "#134e4a", "#0d9488", "#2dd4bf", "#5eead4"].map((c, i) => (
          <div key={i} className="h-3 w-3 rounded-sm" style={{ backgroundColor: c }} />
        ))}
        <span className="text-[10px] text-[var(--text-muted)]">More</span>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Time Range Selector (pills + month picker)
   ═══════════════════════════════════════════════════════════════════════════ */

// The pills drive every number on the page (iteration 203 E1): a range is sent to Ingest as
// from/to, and the active dates are shown next to the pills so it is clear what is being added up.
function TimeRangeSelector({
  period,
  onPeriodChange,
  rangeMode,
  onRangeModeChange,
  months,
  activeRange,
  staleNote,
}: {
  period: string | null;
  onPeriodChange: (v: string) => void;
  rangeMode: RangeMode;
  onRangeModeChange: (v: RangeMode) => void;
  months: string[];
  /** The dates a range pill stands for ("1 Aug – 20 Oct 2026"), shown in range mode. */
  activeRange: string | null;
  /** "Latest data: 28 Sep 2026" when the newest data is not from this month (E2 M2). */
  staleNote: string | null;
}) {
  const pills: { id: RangeMode; label: string; title: string }[] = [
    { id: "month", label: "Month", title: "One calendar month" },
    { id: "3M", label: "3M", title: "This month and the two before it" },
    { id: "6M", label: "6M", title: "This month and the five before it" },
    { id: "1Y", label: "1Y", title: "This month and the eleven before it" },
    { id: "all", label: "All", title: "Everything since the first transaction" },
  ];

  return (
    <div className="flex flex-col items-start gap-1.5 sm:items-end">
      <div className="flex flex-wrap items-center gap-2">
        <div
          className="flex items-center rounded-xl border border-[var(--border-subtle)] bg-[var(--stat-bg)] p-1"
          role="group"
          aria-label="Date range"
        >
          {pills.map((p) => (
            <button
              key={p.id}
              onClick={() => onRangeModeChange(p.id)}
              title={p.title}
              aria-pressed={rangeMode === p.id}
              className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition-all ${
                rangeMode === p.id
                  ? "bg-[var(--accent-blue)] text-white shadow-md"
                  : "text-[var(--text-muted)] hover:text-[var(--foreground)]"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
        {rangeMode === "month" && period ? (
          <motion.select
            aria-label="Select reporting period"
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            value={period}
            onChange={(e) => onPeriodChange(e.target.value)}
            className="cursor-pointer rounded-lg border border-[var(--border)] bg-[var(--input)] px-3 py-1.5 text-xs font-medium text-[var(--foreground)] focus:ring-2 focus:ring-[var(--ring)] focus:outline-none"
          >
            {months.map((m) => (
              <option key={m} value={m}>
                {monthLabel(m)}
              </option>
            ))}
          </motion.select>
        ) : activeRange ? (
          <span
            className="rounded-lg border border-[var(--border-subtle)] px-3 py-1.5 font-mono text-xs text-[var(--foreground-secondary)]"
            data-role="active-range"
          >
            {activeRange}
          </span>
        ) : null}
      </div>
      {staleNote && (
        <span className="text-[11px] text-amber-600 dark:text-amber-400" data-role="stale-note">
          {staleNote} — nothing newer has been captured
        </span>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Tab Bar
   ═══════════════════════════════════════════════════════════════════════════ */

const TABS: { id: TabId; label: string; icon: React.ElementType }[] = [
  { id: "overview", label: "Overview", icon: PieChartIcon },
  { id: "categories", label: "Categories", icon: BarChart3 },
  { id: "food", label: "Food", icon: Utensils },
  { id: "trends", label: "Trends", icon: TrendingUp },
];

/* ═══════════════════════════════════════════════════════════════════════════
   Day Transactions Panel
   ═══════════════════════════════════════════════════════════════════════════ */

function DayTransactionsPanel({ date, onClose }: { date: string; onClose: () => void }) {
  const [transactions, setTransactions] = useState<DayTransaction[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  // iter-18 CH1: "loading" is derived from which date the data belongs to instead of a
  // synchronous setLoading(true) inside the effect (react-hooks/set-state-in-effect).
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  // E3: a re-filed row bumps the page's data version; the day's rows refetch in place (the
  // spinner is only for a new day or a retry).
  const { version } = useDataVersion();
  const key = versionedKey(`${date}#${attempt}`, version);
  const loading = !loadedKey || !loadedKey.startsWith(`${date}#${attempt}#`);

  useEffect(() => {
    let cancelled = false;
    getJson(`/api/expenses/dashboard/day-transactions?date=${date}`)
      .then((d) => {
        if (cancelled) return;
        setTransactions(d.transactions || []);
        setTotal(d.total || 0);
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) setError(errorMessage(e));
      })
      .finally(() => {
        if (!cancelled) setLoadedKey(key);
      });
    return () => {
      cancelled = true;
    };
  }, [date, key]);

  const formattedDate = new Date(date + "T00:00:00").toLocaleDateString("en-IN", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });

  return (
    <motion.div
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      className="glass-card flex h-full flex-col p-4"
    >
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h4 className="text-sm font-bold text-[var(--foreground)]">{formattedDate}</h4>
          <div className="mt-0.5 text-xs text-[var(--text-muted)]">
            {transactions.length} transactions &middot; {autoFormatCurrency(total)}
          </div>
        </div>
        <button
          onClick={onClose}
          className="rounded-md p-1 transition-colors hover:bg-[var(--card-hover)]"
        >
          <X className="h-4 w-4 text-[var(--text-muted)]" />
        </button>
      </div>
      {loading ? (
        <div className="flex flex-1 items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-[var(--text-muted)]" />
        </div>
      ) : error ? (
        <ErrorState compact message={error} onRetry={() => setAttempt((a) => a + 1)} />
      ) : transactions.length === 0 ? (
        <div className="flex flex-1 items-center justify-center text-xs text-[var(--text-muted)]">
          No transactions
        </div>
      ) : (
        <div className="flex-1 space-y-1 overflow-y-auto pr-1">
          {/* Signed: a refund shows "+" and green, so the rows add up to the header (E2 M3). */}
          {transactions.map((t, i) => (
            <TxnRow
              key={t.id}
              txnId={t.id}
              index={i}
              leading={<CategoryBadgeIcon category={t.category} />}
              title={t.merchant || t.raw_description || "Unknown"}
              meta={
                <>
                  <span style={{ color: getCategoryColor(t.category) }}>{t.category}</span>
                  {t.card_last4 && <span> &middot; Card {t.card_last4}</span>}
                </>
              }
              signedAmount={signedOf(t)}
              actions={
                <ChangeCategoryButton
                  txnId={t.id}
                  title={t.merchant || t.raw_description || "Unknown"}
                  category={t.category}
                  amount={signedOf(t)}
                />
              }
            />
          ))}
        </div>
      )}
    </motion.div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Overview Tab
   ═══════════════════════════════════════════════════════════════════════════ */

function OverviewTab({
  data,
  heatmap,
  heatmapSpan,
  heatmapError,
  onRetryHeatmap,
  recurring,
  sel,
  span,
  dataSince,
  onOpenCategory,
  onSelectMonth,
  onOpenOutflows,
}: {
  data: OverviewData;
  heatmap: HeatmapDay[];
  heatmapSpan: DateRange;
  heatmapError: string | null;
  onRetryHeatmap: () => void;
  recurring: RecurringItem[];
  sel: Selection;
  /** The selection's own window: what the headline adds up. */
  span: DateRange;
  dataSince: string | null;
  /** C1: a donut slice / legend row opens the Categories tab on that category. */
  onOpenCategory: (category: string) => void;
  /** C6 / E1: a month row or month bar opens that month. */
  onSelectMonth: (period: string) => void;
  /** E3: Sent to people / Investments / Loan repayments open their payments (to re-file one). */
  onOpenOutflows: () => void;
}) {
  // Spending = everyday spend net of refunds, compared with the same days of last month (or, for
  // a range, the window of equal length before it); transfers to people and investments are
  // shown beside it, not inside it (H5b/M13).
  const headline = overviewHeadline(data);
  const showOutsideSpend =
    headline.refunds > 0 ||
    headline.sentToPeople !== null ||
    headline.investments !== null ||
    headline.loans !== null;

  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [showAllTxns, setShowAllTxns] = useState(false);
  const [activeDonutIndex, setActiveDonutIndex] = useState<number | null>(null);
  const [activeBarIndex, setActiveBarIndex] = useState<number | null>(null);
  const heatmapRef = useRef<HTMLDivElement>(null);

  // E2 M6: Ingest's list is already active-only and carries each item's frequency; the Overview
  // no longer re-filters it with its own cutoffs or guesses "yearly" from one month of charges,
  // so this headline equals the Recurring page's "Total Monthly".
  const totalRecurring = totalMonthly(recurring);

  // E1 AC3: one bar per day for a month, one bar per calendar month for a longer range.
  const bars = spendBars(data.daily_spend, span);
  const monthlyBars = bars.length > 0 && bars[0].kind === "month";
  const barSize = bars.length <= 7 ? 24 : bars.length <= 15 ? 14 : 8;

  const donutData = data.categories.slice(0, 10).map((c) => ({
    name: c.category,
    value: c.total,
    fill: getCategoryColor(c.category),
  }));
  const otherTotal = data.categories.slice(10).reduce((s, c) => s + c.total, 0);
  if (otherTotal > 0) {
    donutData.push({ name: "Other", value: otherTotal, fill: "#475569" });
  }
  const openSlice = (name: string | undefined) => {
    if (name && name !== "Other") onOpenCategory(name);
  };

  // Heatmap monthly summary stats, newest month first
  const heatmapStats = useMemo(() => {
    const byMonth = new Map<string, { total: number; days: number; max: number }>();
    heatmap.forEach((d) => {
      const mk = d.date.slice(0, 7);
      const cur = byMonth.get(mk) || { total: 0, days: 0, max: 0 };
      cur.total += d.total;
      cur.days += 1;
      cur.max = Math.max(cur.max, d.total);
      byMonth.set(mk, cur);
    });
    return Array.from(byMonth.entries())
      .sort(([a], [b]) => b.localeCompare(a))
      .map(([month, stats]) => ({
        month,
        label: new Date(month + "-01T00:00:00").toLocaleDateString("en-IN", {
          month: "short",
          year: "2-digit",
        }),
        ...stats,
        avg: stats.total / Math.max(1, stats.days),
      }));
  }, [heatmap]);
  const monthTotals = heatmapStats.map((s) => s.total);

  // C3: a bar click opens the day panel in the heatmap card further down; bring it into view.
  const openDay = (day: string) => {
    setSelectedDay((prev) => (prev === day ? null : day));
    heatmapRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  return (
    <div className="space-y-6">
      {/* Hero Metrics — Gradient 3D stat cards */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <GradientStatCard
          icon={Wallet}
          title="Spending"
          gradientKey="blue"
          delay={0}
          value={headline.spending}
          sub={headline.comparison}
        />

        <GradientStatCard
          icon={Activity}
          title="Daily Average"
          gradientKey="purple"
          delay={1}
          value={headline.dailyAvg}
          sub={headline.dailyAvgNote}
        />

        <GradientStatCard
          icon={Flame}
          title="Highest Day"
          gradientKey="red"
          delay={2}
          value={data.highest_day?.total || 0}
          sub={data.highest_day ? shortDate(data.highest_day.date, !!data.range) : "—"}
        />

        <GradientStatCard
          icon={Hash}
          title="Transactions"
          gradientKey="green"
          delay={3}
          value={data.txn_count}
          isCurrency={false}
          sub={`avg ${autoFormatCurrency(data.avg_txn)} each`}
        />

        <GradientStatCard
          icon={getCategoryIcon(data.top_category?.category || "")}
          title="Top Category"
          gradientKey="amber"
          delay={4}
          value={data.top_category?.total || 0}
          displayText={data.top_category?.category || "—"}
          sub={
            data.top_category
              ? `${autoFormatCurrency(data.top_category.total)} · ${data.top_category.count} txns`
              : ""
          }
        />
      </div>

      {/* Money that is visible but not mixed into Spending (contract 4; absent on an
          older Ingest, so the row is simply not rendered there). */}
      {showOutsideSpend && (
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="glass-card flex flex-col gap-3 px-5 py-3 sm:flex-row sm:flex-wrap sm:items-center sm:gap-x-10"
          data-role="outside-spend"
        >
          {headline.refunds > 0 && (
            <OutsideSpendItem
              icon={RotateCcw}
              label="Refunds"
              value={headline.refunds}
              note="netted from Spending"
            />
          )}
          {headline.sentToPeople && (
            <OutsideSpendItem
              icon={Send}
              label="Sent to people"
              value={headline.sentToPeople.total}
              note={`${headline.sentToPeople.count} transfer${headline.sentToPeople.count !== 1 ? "s" : ""} · not in Spending`}
              onOpen={onOpenOutflows}
            />
          )}
          {headline.investments && (
            <OutsideSpendItem
              icon={Landmark}
              label="Investments"
              value={headline.investments.total}
              note={`${headline.investments.count} payment${headline.investments.count !== 1 ? "s" : ""} · not in Spending`}
              onOpen={onOpenOutflows}
            />
          )}
          {headline.loans && (
            <OutsideSpendItem
              icon={Building2}
              label="Loan repayments"
              value={headline.loans.total}
              note={`${headline.loans.count} repayment${headline.loans.count !== 1 ? "s" : ""} · not in Spending`}
              onOpen={onOpenOutflows}
            />
          )}
        </motion.div>
      )}

      {/* Category Donut + Spending chart */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.25 }}
          className="glass-card p-5"
        >
          <h3 className="mb-1 text-sm font-bold tracking-wider text-[var(--foreground)] uppercase">
            Spending Breakdown
          </h3>
          <div className="mb-3 text-[10px] text-[var(--text-muted)]">
            Click a category to see its merchants and transactions
          </div>
          <div className="flex flex-col items-center gap-4 md:flex-row">
            <div className="h-64 w-64 flex-shrink-0">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <defs>
                    {donutData.map((e, i) => (
                      <g key={`defs-${i}`}>
                        <filter id={`donut-glow-${i}`}>
                          <feGaussianBlur stdDeviation="4" result="blur" />
                          <feFlood floodColor={e.fill} floodOpacity="0.6" />
                          <feComposite in2="blur" operator="in" />
                          <feMerge>
                            <feMergeNode />
                            <feMergeNode in="SourceGraphic" />
                          </feMerge>
                        </filter>
                        <radialGradient id={`donut-depth-${i}`} cx="50%" cy="35%" r="80%">
                          <stop offset="0%" stopColor={e.fill} stopOpacity={1} />
                          <stop offset="55%" stopColor={e.fill} stopOpacity={0.9} />
                          <stop offset="100%" stopColor={e.fill} stopOpacity={0.55} />
                        </radialGradient>
                      </g>
                    ))}
                    <filter id="donut-dropshadow" x="-20%" y="-20%" width="140%" height="140%">
                      <feGaussianBlur in="SourceAlpha" stdDeviation="6" />
                      <feOffset dy="8" />
                      <feComponentTransfer>
                        <feFuncA type="linear" slope="0.4" />
                      </feComponentTransfer>
                      <feMerge>
                        <feMergeNode />
                        <feMergeNode in="SourceGraphic" />
                      </feMerge>
                    </filter>
                  </defs>
                  <Pie
                    data={donutData}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius={58}
                    outerRadius={102}
                    paddingAngle={2}
                    animationBegin={300}
                    animationDuration={800}
                    filter="url(#donut-dropshadow)"
                    onMouseEnter={(_, i) => setActiveDonutIndex(i)}
                    onMouseLeave={() => setActiveDonutIndex(null)}
                    onClick={(_, i) => openSlice(donutData[i]?.name)}
                    label={({ cx, cy, midAngle, innerRadius, outerRadius, percent, index }) => {
                      if (percent < 0.06) return null;
                      const RADIAN = Math.PI / 180;
                      const isActive = activeDonutIndex === index;
                      const radius =
                        innerRadius + (outerRadius - innerRadius) * (isActive ? 0.5 : 0.5);
                      const x = cx + radius * Math.cos(-midAngle * RADIAN);
                      const y = cy + radius * Math.sin(-midAngle * RADIAN);
                      return (
                        <text
                          x={x}
                          y={y}
                          fill="#fff"
                          textAnchor="middle"
                          dominantBaseline="central"
                          fontSize={isActive ? 13 : 11}
                          fontWeight={700}
                          style={{
                            textShadow: "0 1px 3px rgba(0,0,0,0.5)",
                            transition: "font-size 0.2s ease",
                            pointerEvents: "none",
                          }}
                        >
                          {(percent * 100).toFixed(0)}%
                        </text>
                      );
                    }}
                    labelLine={false}
                  >
                    {donutData.map((e, i) => (
                      <Cell
                        key={i}
                        fill={`url(#donut-depth-${i})`}
                        stroke={activeDonutIndex === i ? e.fill : "rgba(0,0,0,0.35)"}
                        strokeWidth={activeDonutIndex === i ? 2 : 1}
                        style={{
                          filter: activeDonutIndex === i ? `url(#donut-glow-${i})` : "none",
                          transform: activeDonutIndex === i ? "scale(1.06)" : "scale(1)",
                          transformOrigin: "center",
                          transition: "all 0.25s cubic-bezier(0.4, 0, 0.2, 1)",
                          opacity: activeDonutIndex !== null && activeDonutIndex !== i ? 0.4 : 1,
                          // "Other" groups many categories and has no single page to open (C1).
                          cursor: e.name === "Other" ? "default" : "pointer",
                        }}
                      />
                    ))}
                  </Pie>
                  <DonutCenter
                    total={
                      activeDonutIndex !== null
                        ? donutData[activeDonutIndex]?.value || data.total
                        : data.total
                    }
                    label={
                      activeDonutIndex !== null ? donutData[activeDonutIndex]?.name : undefined
                    }
                  />
                  <RechartsTooltip content={<ChartTooltip />} />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="max-h-64 w-full flex-1 space-y-1.5 overflow-y-auto">
              {data.categories.map((cat, catIdx) => {
                const pct = data.total > 0 ? ((cat.total / data.total) * 100).toFixed(1) : "0.0";
                const Icon = getCategoryIcon(cat.category);
                const isHighlighted = activeDonutIndex === catIdx;
                return (
                  <button
                    type="button"
                    key={cat.category}
                    className={`group flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left transition-all duration-200 hover:bg-[var(--card-hover)] ${
                      isHighlighted ? "scale-[1.02] bg-[var(--card-hover)]" : ""
                    } ${activeDonutIndex !== null && !isHighlighted ? "opacity-40" : ""}`}
                    onMouseEnter={() => setActiveDonutIndex(catIdx < 10 ? catIdx : null)}
                    onMouseLeave={() => setActiveDonutIndex(null)}
                    onClick={() => onOpenCategory(cat.category)}
                  >
                    <div
                      className="h-2.5 w-2.5 flex-shrink-0 rounded-full"
                      style={{ backgroundColor: getCategoryColor(cat.category) }}
                    />
                    <Icon className="h-3.5 w-3.5 flex-shrink-0 text-[var(--text-muted)]" />
                    <span className="flex-1 truncate text-xs font-medium text-[var(--foreground)]">
                      {cat.category}
                    </span>
                    <span className="font-mono text-xs font-semibold text-[var(--foreground)]">
                      {autoFormatCurrency(cat.total)}
                    </span>
                    <span className="w-10 text-right text-[10px] text-[var(--text-muted)]">
                      {pct}%
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        </motion.div>

        {/* Spending chart — days for a month, months for a longer range (E1 AC3) */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
          className="glass-card p-5"
        >
          <h3 className="mb-4 text-sm font-bold tracking-wider text-[var(--foreground)] uppercase">
            {monthlyBars ? "Monthly Spending" : "Daily Spending"}
          </h3>
          <div className="mb-2 text-[10px] text-[var(--text-muted)]">
            {monthlyBars
              ? "Click a month to open it"
              : "Click a bar to see that day's transactions"}
          </div>
          <ResponsiveContainer width="100%" height={210}>
            <BarChart data={bars} barSize={barSize}>
              <defs>
                <linearGradient id="barGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--accent-blue)" stopOpacity={1} />
                  <stop offset="100%" stopColor="var(--accent-blue)" stopOpacity={0.4} />
                </linearGradient>
                <linearGradient id="barGradActive" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#f59e0b" stopOpacity={1} />
                  <stop offset="100%" stopColor="#f59e0b" stopOpacity={0.5} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" vertical={false} />
              <XAxis
                dataKey="label"
                tick={{ fontSize: 10, fill: "var(--text-muted)" }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tick={{ fontSize: 10, fill: "var(--text-muted)" }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v) => (v >= 1000 ? `${(v / 1000).toFixed(0)}k` : v)}
              />
              <RechartsTooltip
                content={<ChartTooltip />}
                cursor={{ fill: "var(--stat-bg)", radius: 4 }}
              />
              <Bar
                dataKey="total"
                name="Spend"
                radius={[3, 3, 0, 0]}
                animationBegin={400}
                animationDuration={600}
                onClick={(d: any) => {
                  const bar = (d?.payload ?? d) as { key?: string; kind?: string; total?: number };
                  if (!bar?.key) return;
                  if (bar.kind === "month") onSelectMonth(bar.key);
                  else if ((bar.total ?? 0) !== 0) openDay(bar.key);
                }}
                onMouseEnter={(_, i) => setActiveBarIndex(i)}
                onMouseLeave={() => setActiveBarIndex(null)}
                style={{ cursor: "pointer" }}
              >
                {bars.map((b, i) => (
                  <Cell
                    key={b.key}
                    fill={selectedDay === b.key ? "url(#barGradActive)" : "url(#barGrad)"}
                    opacity={activeBarIndex !== null && activeBarIndex !== i ? 0.55 : 1}
                    style={{ transition: "opacity 0.2s ease" }}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </motion.div>
      </div>

      {/* Heatmap + Monthly Summary Sidebar */}
      <motion.div
        ref={heatmapRef}
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.35 }}
        className="glass-card scroll-mt-24 p-5"
        data-role="heatmap-card"
      >
        <h3 className="mb-4 text-sm font-bold tracking-wider text-[var(--foreground)] uppercase">
          Spending Heatmap
          <span className="ml-2 text-[10px] font-normal tracking-normal text-[var(--text-muted)] normal-case">
            {windowLabel(heatmapSpan)} · click a day to see its transactions
          </span>
        </h3>
        <div
          className={`grid gap-5 transition-all duration-300 ${selectedDay ? "grid-cols-1 lg:grid-cols-[3fr_2fr]" : "grid-cols-1 lg:grid-cols-[3fr_1fr]"}`}
        >
          {heatmapError ? (
            <ErrorState compact message={heatmapError} onRetry={onRetryHeatmap} />
          ) : (
            <CalendarHeatmap
              data={heatmap}
              span={heatmapSpan}
              onDayClick={(d) => setSelectedDay((prev) => (prev === d ? null : d))}
              selectedDate={selectedDay}
            />
          )}
          {selectedDay ? (
            <DayTransactionsPanel date={selectedDay} onClose={() => setSelectedDay(null)} />
          ) : (
            <div className="space-y-2">
              <h4 className="mb-3 text-xs font-bold tracking-wider text-[var(--text-muted)] uppercase">
                Monthly Summary
              </h4>
              <div className="max-h-72 space-y-1 overflow-y-auto pr-1">
                {heatmapStats.map((s, i) => (
                  <motion.button
                    type="button"
                    key={s.month}
                    initial={{ opacity: 0, x: 10 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: 0.4 + Math.min(i, 12) * 0.05 }}
                    className={`flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-[var(--card-hover)] ${
                      sel.mode === "month" && sel.period === s.month ? "bg-[var(--card-hover)]" : ""
                    }`}
                    onClick={() => onSelectMonth(s.month)}
                    title={`Open ${monthLabel(s.month)}`}
                  >
                    <span className="w-10 font-mono text-[10px] text-[var(--text-muted)]">
                      {s.label}
                    </span>
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--stat-bg)]">
                      {/* scaled to the biggest month, not the newest (E2 M5) */}
                      <div
                        className="h-full rounded-full bg-teal-500/60"
                        style={{ width: `${barWidthPct(s.total, monthTotals)}%` }}
                      />
                    </div>
                    <span className="w-16 text-right font-mono text-[10px] font-semibold text-[var(--foreground)]">
                      {autoFormatCurrency(s.total)}
                    </span>
                  </motion.button>
                ))}
              </div>
            </div>
          )}
        </div>
      </motion.div>

      {/* Top Transactions + Recurring Summary (side by side) */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          className="glass-card p-5"
        >
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-sm font-bold tracking-wider text-[var(--foreground)] uppercase">
              Biggest Transactions
            </h3>
            <button
              onClick={(e) => {
                e.stopPropagation();
                setShowAllTxns(true);
              }}
              className="flex items-center gap-1 rounded-lg px-2.5 py-1 text-[10px] font-semibold text-[var(--accent-blue)] transition-colors hover:bg-[var(--accent-blue)]/10"
            >
              View All <ArrowRight className="h-3 w-3" />
            </button>
          </div>
          <div className="space-y-1">
            {data.top_transactions.slice(0, 7).map((t, i) => (
              <TxnRow
                key={t.id}
                txnId={t.id}
                index={i}
                leading={
                  <span className="w-4 font-mono text-[10px] text-[var(--text-muted)]">
                    {i + 1}
                  </span>
                }
                title={t.merchant || t.raw_description || "Unknown"}
                meta={
                  <>
                    {shortDate(t.date, !!data.range)} &middot;{" "}
                    <span style={{ color: getCategoryColor(t.category) }}>{t.category}</span>
                  </>
                }
                signedAmount={t.amount}
                actions={
                  <ChangeCategoryButton
                    txnId={t.id}
                    title={t.merchant || t.raw_description || "Unknown"}
                    category={t.category}
                    amount={t.amount}
                  />
                }
              />
            ))}
          </div>
        </motion.div>

        {/* Recurring Payments */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.45 }}
          className="glass-card p-5"
        >
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-bold tracking-wider text-[var(--foreground)] uppercase">
              Recurring Payments
            </h3>
            <Link
              href="/expenses/recurring"
              className="flex items-center gap-1 rounded-lg px-2.5 py-1 text-[10px] font-semibold text-[var(--accent-blue)] transition-colors hover:bg-[var(--accent-blue)]/10"
            >
              View all <ArrowRight className="h-3 w-3" />
            </Link>
          </div>
          <div className="mb-4 flex items-baseline gap-2">
            <span className="text-2xl font-bold text-[var(--foreground)]">
              {autoFormatCurrency(totalRecurring)}
            </span>
            <span className="text-xs text-[var(--text-muted)]">/month</span>
          </div>
          <div className="space-y-1.5">
            {recurring.map((r, i) => (
              <motion.div
                key={`${r.merchant}-${i}`}
                initial={{ opacity: 0, x: 10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.5 + Math.min(i, 15) * 0.02 }}
                className="flex items-center gap-2.5 rounded-lg px-2 py-1.5"
              >
                <div className="flex h-6 w-6 flex-shrink-0 items-center justify-center overflow-hidden rounded-md bg-[var(--stat-bg)]">
                  {r.logo_domain ? (
                    <img src={getMerchantLogo(r.logo_domain)!} alt="" className="h-4 w-4" />
                  ) : (
                    <Repeat className="h-3 w-3 text-[var(--text-muted)]" />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate text-xs font-medium text-[var(--foreground)]">
                      {r.type === "emi" && r.product_name
                        ? r.product_name
                        : r.display_name || r.merchant}
                    </span>
                    {r.type === "emi" && (
                      <span className="flex-shrink-0 rounded bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-bold text-amber-400">
                        EMI
                      </span>
                    )}
                  </div>
                  <div className="text-[10px] text-[var(--text-muted)]">
                    {r.type === "emi" && r.payments_done && r.total_installments
                      ? `${r.payments_done}/${r.total_installments} paid`
                      : r.next_expected
                        ? `${cadenceLabel(r)} · next ${shortDate(r.next_expected)}`
                        : cadenceLabel(r)}
                  </div>
                </div>
                <span className="flex-shrink-0 font-mono text-xs font-semibold text-[var(--foreground)]">
                  {autoFormatCurrency(r.avg_amount)}
                  <span className="text-[9px] font-normal text-[var(--text-muted)]">
                    {frequencyLabel(r)}
                  </span>
                </span>
              </motion.div>
            ))}
          </div>
        </motion.div>
      </div>

      {showAllTxns && (
        <AllTransactionsModal
          sel={sel}
          dataSince={dataSince}
          span={span}
          onClose={() => setShowAllTxns(false)}
        />
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   View All — every transaction behind the Spending figure, paged (E2 M1)
   ═══════════════════════════════════════════════════════════════════════════ */

const ALL_TXNS_PAGE = 50;

function AllTransactionsModal({
  sel,
  dataSince,
  span,
  onClose,
}: {
  sel: Selection;
  dataSince: string | null;
  span: DateRange;
  onClose: () => void;
}) {
  const [sort, setSort] = useState<"amount" | "date">("amount");
  const [rows, setRows] = useState<ListTransaction[]>([]);
  const [totalCount, setTotalCount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  // E3: after a row is re-filed the page's data version moves; the list refetches the rows it
  // already shows (same sort, same length), so a row that left Spending leaves this list too.
  const { version } = useDataVersion();
  const shownRows = useRef(0);
  useEffect(() => {
    shownRows.current = rows.length;
  }, [rows.length]);

  const loadMore = async () => {
    setLoading(true);
    try {
      const d = await getJson(
        allTransactionsUrl(sel, new Date(), dataSince, sort, rows.length, ALL_TXNS_PAGE)
      );
      setRows((prev) => [...prev, ...(d.transactions || [])]);
      setTotalCount(d.total_count ?? null);
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    const limit = Math.max(ALL_TXNS_PAGE, shownRows.current);
    getJson(allTransactionsUrl(sel, new Date(), dataSince, sort, 0, limit))
      .then((d) => {
        if (cancelled) return;
        setRows(d.transactions || []);
        setTotalCount(d.total_count ?? null);
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) setError(errorMessage(e));
      });
    return () => {
      cancelled = true;
    };
    // sel / dataSince are fixed while the modal is open
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sort, attempt, version]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <motion.div
        role="dialog"
        aria-label="All spending transactions"
        initial={{ opacity: 0, scale: 0.95, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        className="glass-card mx-4 max-h-[80vh] w-full max-w-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-6 py-4">
          <div>
            <h3 className="text-sm font-bold tracking-wider text-[var(--foreground)] uppercase">
              All Spending Transactions
            </h3>
            <p className="mt-0.5 text-xs text-[var(--text-muted)]">
              {windowLabel(span)}
              {totalCount !== null && ` · ${totalCount} transaction${totalCount !== 1 ? "s" : ""}`}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center rounded-lg border border-[var(--border-subtle)] bg-[var(--stat-bg)] p-0.5">
              {(["amount", "date"] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => {
                    if (s !== sort) {
                      setRows([]);
                      setTotalCount(null);
                      setSort(s);
                    }
                  }}
                  aria-pressed={sort === s}
                  className={`rounded-md px-2 py-1 text-[10px] font-semibold transition-all ${
                    sort === s
                      ? "bg-[var(--accent-blue)] text-white"
                      : "text-[var(--text-muted)] hover:text-[var(--foreground)]"
                  }`}
                >
                  {s === "amount" ? "By amount" : "Newest first"}
                </button>
              ))}
            </div>
            <button
              onClick={onClose}
              aria-label="Close"
              className="rounded-lg p-1.5 transition-colors hover:bg-[var(--card-hover)]"
            >
              <X className="h-4 w-4 text-[var(--text-muted)]" />
            </button>
          </div>
        </div>
        <div className="max-h-[60vh] overflow-y-auto px-6 py-3">
          {error ? (
            <ErrorState
              compact
              message={error}
              onRetry={() => {
                setRows([]);
                setError(null);
                setTotalCount(null);
                setAttempt((a) => a + 1);
              }}
            />
          ) : totalCount === null ? (
            <div className="flex justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--text-muted)]" />
            </div>
          ) : rows.length === 0 ? (
            <div className="py-8 text-center text-xs text-[var(--text-muted)]">
              No spending in this period
            </div>
          ) : (
            <div className="space-y-1">
              {rows.map((t, i) => (
                <TxnRow
                  key={t.id}
                  txnId={t.id}
                  index={Math.min(i, 10)}
                  leading={
                    <>
                      <span className="w-6 font-mono text-[10px] text-[var(--text-muted)]">
                        {i + 1}
                      </span>
                      <CategoryBadgeIcon category={t.category} />
                    </>
                  }
                  title={t.merchant || t.raw_description || "Unknown"}
                  meta={
                    <>
                      {shortDate(t.date, true)} &middot;{" "}
                      <span style={{ color: getCategoryColor(t.category || "") }}>
                        {t.category}
                      </span>
                      {t.source && <span> &middot; {t.source}</span>}
                    </>
                  }
                  signedAmount={signedOf(t)}
                  actions={
                    <ChangeCategoryButton
                      txnId={t.id}
                      title={t.merchant || t.raw_description || "Unknown"}
                      category={t.category}
                      amount={signedOf(t)}
                    />
                  }
                />
              ))}
              {totalCount !== null && rows.length < totalCount && (
                <div className="flex justify-center pt-3">
                  <button
                    onClick={loadMore}
                    disabled={loading}
                    className="flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs font-medium text-[var(--foreground-secondary)] transition-colors hover:bg-[var(--card-hover)] disabled:opacity-50"
                  >
                    {loading && <Loader2 className="h-3 w-3 animate-spin" />}
                    Show {Math.min(ALL_TXNS_PAGE, totalCount - rows.length)} more of{" "}
                    {totalCount - rows.length}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </motion.div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Categories Tab
   ═══════════════════════════════════════════════════════════════════════════ */

// Guess merchant logo domain from name
function guessDomain(name: string): string | null {
  const map: Record<string, string> = {
    Swiggy: "swiggy.com",
    Zomato: "zomato.com",
    Amazon: "amazon.in",
    Flipkart: "flipkart.com",
    Blinkit: "blinkit.com",
    BigBasket: "bigbasket.com",
    Zepto: "zeptonow.com",
    Uber: "uber.com",
    Netflix: "netflix.com",
    Spotify: "spotify.com",
    Hotstar: "hotstar.com",
    CRED: "cred.club",
    Rapido: "rapido.bike",
    Myntra: "myntra.com",
    Nykaa: "nykaa.com",
    BookMyShow: "bookmyshow.com",
    MakeMyTrip: "makemytrip.com",
    "Swiggy Instamart": "swiggy.com",
    PharmEasy: "pharmeasy.in",
    "Urban Company": "urbancompany.com",
    IRCTC: "irctc.co.in",
    Dominos: "dominos.co.in",
    Starbucks: "starbucks.in",
  };
  for (const [key, domain] of Object.entries(map)) {
    if (name.toLowerCase().includes(key.toLowerCase())) return domain;
  }
  return null;
}

/**
 * Fetch `url` whenever it (or `attempt`) changes; `loading` is derived from which url the data
 * belongs to, so the effect never sets state synchronously (iter-18 CH1). Null url = nothing.
 *
 * `version` is the page's data version (E3): when only it moves (a row was re-filed), the data is
 * fetched again but the previous answer stays on screen until the new one lands — a refresh in
 * place, not a spinner.
 */
function useJson<T>(url: string | null, attempt = 0, version = 0) {
  const [state, setState] = useState<{
    key: string;
    base: string;
    data: T | null;
    error: string | null;
  } | null>(null);
  const base = url ? `${url}#${attempt}` : null;
  const key = base ? versionedKey(base, version) : null;
  useEffect(() => {
    if (!url || !key || !base) return;
    let cancelled = false;
    getJson<T>(url)
      .then((data) => {
        if (!cancelled) setState({ key, base, data, error: null });
      })
      .catch((e) => {
        if (!cancelled) setState({ key, base, data: null, error: errorMessage(e) });
      });
    return () => {
      cancelled = true;
    };
  }, [url, key, base]);
  const current = state && state.key === key ? state : null;
  const previous = !current && state && state.base === base && !state.error ? state : null;
  return {
    loading: !!key && !current && !previous,
    data: (current ?? previous)?.data ?? null,
    error: current?.error ?? null,
  };
}

interface CategoryDrill {
  category: string;
  group: string;
  total: number;
  merchants: { name: string; total: number; count: number; refunds?: number }[];
  recent_transactions?: ListTransaction[];
}

interface MerchantDrill {
  merchant: string;
  total: number;
  refunds: number;
  count: number;
  avg_transaction: number;
  range?: DateRange;
  monthly: { month: string; total: number; count: number }[];
  transactions: ListTransaction[];
}

/**
 * E3: the categories counted beside Spending (Sent to people, Investments, Loan repayments) in the
 * window, as cards built from the same category drill as the spending cards — so a payment to a
 * person (a maid, a driver) can be opened and re-filed like any spend. The spending list never
 * carried them.
 */
function useOutflowCards(
  sel: Selection,
  dataSince: string | null,
  version: number,
  attempt: number
) {
  const key = versionedKey(`${categoriesUrl(sel, new Date(), dataSince)}#${attempt}`, version);
  const [state, setState] = useState<{
    key: string;
    cards: OutflowCard[];
    error: string | null;
  } | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const choices = outflowCategoryChoices((await loadCategoryChoices()).categories);
        const drills = await Promise.all(
          choices.map((c) =>
            getJson<CategoryDrill>(categoriesUrl(sel, new Date(), dataSince, c.name))
          )
        );
        if (!cancelled) setState({ key, cards: outflowCards(choices, drills), error: null });
      } catch (e) {
        if (!cancelled) setState({ key, cards: [], error: errorMessage(e) });
      }
    })();
    return () => {
      cancelled = true;
    };
    // `sel` and `dataSince` are part of `key`
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return {
    cards: state?.cards ?? [],
    loaded: !!state,
    error: state && state.key === key ? state.error : null,
  };
}

function CategoriesTab({
  sel,
  dataSince,
  focusCategory,
  focusOutflows = false,
}: {
  sel: Selection;
  dataSince: string | null;
  /** C1: opened from a donut slice — start with that category expanded. */
  focusCategory: string | null;
  /** E3: opened from the Overview's Sent to people / Investments / Loans — scroll to them. */
  focusOutflows?: boolean;
}) {
  const today = new Date();
  const span = selectionWindow(sel, today, dataSince);
  const { version } = useDataVersion();
  const [attempt, setAttempt] = useState(0);
  const list = useJson<{ categories: CategoryDetail[] }>(
    categoriesUrl(sel, today, dataSince),
    attempt,
    version
  );
  const categories = list.data?.categories ?? [];
  const [outflowAttempt, setOutflowAttempt] = useState(0);
  const outflows = useOutflowCards(sel, dataSince, version, outflowAttempt);
  const outflowGroupTotal = (group: string) =>
    outflows.cards.filter((c) => c.group === group).reduce((s, c) => s + c.total, 0);
  // Spending cards, then the Outside Spending cards: one card layout, one drill, one control.
  const cards: (CategoryDetail & { group?: string })[] = [...categories, ...outflows.cards];
  const outflowRef = useRef<HTMLDivElement>(null);
  const outflowScrolled = useRef(false);
  useEffect(() => {
    if (focusOutflows && outflows.loaded && outflowRef.current && !outflowScrolled.current) {
      outflowScrolled.current = true;
      outflowRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [focusOutflows, outflows.loaded, list.loading]);

  const [expanded, setExpanded] = useState<string | null>(focusCategory);
  const [detailAttempt, setDetailAttempt] = useState(0);
  const drill = useJson<CategoryDrill>(
    expanded ? categoriesUrl(sel, today, dataSince, expanded) : null,
    detailAttempt,
    version
  );
  const detail = drill.data;

  const [selectedMerchant, setSelectedMerchant] = useState<string | null>(null);
  const [merchantAttempt, setMerchantAttempt] = useState(0);
  const merchantDrill = useJson<MerchantDrill>(
    expanded && selectedMerchant
      ? merchantDetailUrl(sel, today, dataSince, selectedMerchant, expanded)
      : null,
    merchantAttempt,
    version
  );
  const merchantDetail = merchantDrill.data;

  // Bring a category opened from the Overview donut into view once it is on the page.
  const focusRef = useRef<HTMLDivElement>(null);
  const focusedOnce = useRef(false);
  useEffect(() => {
    if (focusCategory && !list.loading && focusRef.current && !focusedOnce.current) {
      focusedOnce.current = true;
      focusRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [focusCategory, list.loading]);

  const totalSpend = categories.reduce((s, c) => s + c.total, 0);

  const toggleCategory = (cat: string) => {
    setExpanded((prev) => (prev === cat ? null : cat));
    setSelectedMerchant(null);
  };

  const toggleMerchant = (merchant: string) => {
    setSelectedMerchant((prev) => (prev === merchant ? null : merchant));
  };

  if (list.loading)
    return (
      <div className="flex justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-[var(--text-muted)]" />
      </div>
    );

  if (list.error)
    return <ErrorState message={list.error} onRetry={() => setAttempt((a) => a + 1)} />;

  if (categories.length === 0)
    return (
      <div className="space-y-4">
        <div className="flex justify-end">
          <PayeeRulesButton />
        </div>
        <div className="py-20 text-center text-sm text-[var(--text-muted)]">
          No spending in {span ? windowLabel(span) : "this period"}
        </div>
      </div>
    );

  return (
    <div className="space-y-6">
      {/* E9: the payee rules made from "change category" — listed and removable here. */}
      <div className="flex items-center justify-between gap-3">
        <p className="text-[11px] text-[var(--text-muted)]">
          Use the tag button on any transaction to file it under another category.
        </p>
        <PayeeRulesButton />
      </div>

      {/* Compact Category Ranking Bars */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass-card p-5"
      >
        <h3 className="mb-3 text-sm font-bold tracking-wider text-[var(--foreground)] uppercase">
          Category Ranking
          {span && (
            <span className="ml-2 text-[10px] font-normal tracking-normal text-[var(--text-muted)] normal-case">
              {windowLabel(span)}
            </span>
          )}
        </h3>
        <div className="space-y-1.5">
          {categories.slice(0, 12).map((cat, i) => {
            const pct = totalSpend > 0 ? (cat.total / totalSpend) * 100 : 0;
            const Icon = getCategoryIcon(cat.category);
            return (
              <motion.button
                type="button"
                key={cat.category}
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: i * 0.02 }}
                className={`group flex w-full cursor-pointer items-center gap-2 rounded-lg px-2 py-1 text-left transition-colors hover:bg-[var(--card-hover)] ${
                  expanded === cat.category ? "bg-[var(--card-hover)]" : ""
                }`}
                onClick={() => toggleCategory(cat.category)}
              >
                <Icon
                  className="h-3.5 w-3.5 flex-shrink-0"
                  style={{ color: getCategoryColor(cat.category) }}
                />
                <span className="w-28 truncate text-[11px] font-medium text-[var(--foreground)]">
                  {cat.category}
                </span>
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--stat-bg)]">
                  <motion.div
                    className="h-full rounded-full"
                    style={{ backgroundColor: getCategoryColor(cat.category) }}
                    initial={{ width: 0 }}
                    animate={{ width: `${Math.max(0, pct)}%` }}
                    transition={{ duration: 0.5, delay: i * 0.02 + 0.15 }}
                  />
                </div>
                <span className="w-20 text-right font-mono text-[11px] font-semibold text-[var(--foreground)]">
                  {autoFormatCurrency(cat.total)}
                </span>
                <span className="w-8 text-right text-[9px] text-[var(--text-muted)]">
                  {pct.toFixed(0)}%
                </span>
              </motion.button>
            );
          })}
        </div>
      </motion.div>

      {/* Category Cards with Rich Drill-down (spending, then Outside Spending — E3) */}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
        {cards.map((cat, i) => {
          const Icon = getCategoryIcon(cat.category);
          const base = cat.group ? outflowGroupTotal(cat.group) : totalSpend;
          const pct = base > 0 ? ((cat.total / base) * 100).toFixed(1) : "0.0";
          const isExpanded = expanded === cat.category;

          return (
            <Fragment key={cat.category}>
              {i === categories.length && (
                <div
                  ref={outflowRef}
                  className="col-span-1 scroll-mt-24 pt-4 md:col-span-2 lg:col-span-3"
                  data-role="outside-spending"
                >
                  <h3 className="text-sm font-bold tracking-wider text-[var(--foreground)] uppercase">
                    Outside Spending
                  </h3>
                  <p className="mt-0.5 text-[11px] text-[var(--text-muted)]">
                    Sent to people, investments and loan repayments — shown beside Spending, not in
                    it. Open one to re-file a payment (say, to a maid or a driver) with its tag
                    button.
                  </p>
                </div>
              )}
              <motion.div
                ref={isExpanded ? focusRef : undefined}
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.03 }}
                className={`glass-card cursor-pointer scroll-mt-24 overflow-hidden transition-all duration-300 ${isExpanded ? "col-span-1 ring-1 ring-[var(--accent-blue)]/20 md:col-span-2 lg:col-span-3" : "hover:ring-1 hover:ring-[var(--border)]"}`}
                onClick={() => toggleCategory(cat.category)}
                data-category={cat.category}
              >
                <div className="p-4">
                  <div className="flex items-center gap-3">
                    <div
                      className="rounded-lg p-2 transition-transform duration-200 group-hover:scale-110"
                      style={{ backgroundColor: getCategoryColor(cat.category) + "18" }}
                    >
                      <Icon className="h-5 w-5" style={{ color: getCategoryColor(cat.category) }} />
                    </div>
                    <div className="flex-1">
                      <div className="text-sm font-bold text-[var(--foreground)]">
                        {cat.category}
                      </div>
                      <div className="text-[10px] text-[var(--text-muted)]">
                        {cat.count} transactions &middot; {pct}%
                        {cat.group && ` of ${outflowGroupLabel(cat.group)}`}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="font-mono text-sm font-bold text-[var(--foreground)]">
                        {autoFormatCurrency(cat.total)}
                      </div>
                    </div>
                    <motion.div
                      animate={{ rotate: isExpanded ? 90 : 0 }}
                      transition={{ duration: 0.2 }}
                    >
                      <ChevronRight className="h-4 w-4 text-[var(--text-muted)]" />
                    </motion.div>
                  </div>
                </div>

                {/* Expanded Rich Drill-down */}
                <AnimatePresence initial={false}>
                  {isExpanded && (
                    <motion.div
                      key="expanded"
                      initial={{ opacity: 0, y: -12 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -12 }}
                      transition={{ duration: 0.28, ease: [0.4, 0, 0.2, 1] }}
                      className="cursor-default overflow-hidden border-t border-[var(--border)] px-4 pb-4"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {drill.loading ? (
                        <div className="flex justify-center py-6">
                          <Loader2 className="h-5 w-5 animate-spin text-[var(--text-muted)]" />
                        </div>
                      ) : drill.error ? (
                        <ErrorState
                          compact
                          message={drill.error}
                          onRetry={() => setDetailAttempt((a) => a + 1)}
                        />
                      ) : detail ? (
                        <div className="space-y-4 pt-4">
                          {/* Merchant Cards with logos */}
                          <div>
                            <h4 className="mb-3 text-xs font-bold tracking-wider text-[var(--text-muted)] uppercase">
                              Merchants
                            </h4>
                            <div className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3">
                              {detail.merchants.map((m, j) => {
                                const domain = guessDomain(m.name);
                                const isSelected = selectedMerchant === m.name;
                                const merchantPct = detail.merchants.reduce(
                                  (s, x) => s + Math.max(0, x.total),
                                  0
                                );
                                const share =
                                  merchantPct > 0 ? (Math.max(0, m.total) / merchantPct) * 100 : 0;
                                return (
                                  <motion.button
                                    type="button"
                                    key={m.name}
                                    initial={{ opacity: 0, scale: 0.95 }}
                                    animate={{ opacity: 1, scale: 1 }}
                                    transition={{ delay: j * 0.03 }}
                                    className={`cursor-pointer rounded-xl border p-3 text-left transition-all ${
                                      isSelected
                                        ? "border-[var(--accent-blue)]/40 bg-[var(--accent-blue)]/5"
                                        : "border-[var(--border-subtle)] bg-[var(--stat-bg)] hover:border-[var(--border)]"
                                    }`}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      toggleMerchant(m.name);
                                    }}
                                  >
                                    <div className="flex items-center gap-2.5">
                                      <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--card)]">
                                        {domain ? (
                                          <img
                                            src={getMerchantLogo(domain)!}
                                            alt=""
                                            className="h-5 w-5"
                                          />
                                        ) : (
                                          <ShoppingBag className="h-4 w-4 text-[var(--text-muted)]" />
                                        )}
                                      </div>
                                      <div className="min-w-0 flex-1">
                                        <div className="truncate text-xs font-semibold text-[var(--foreground)]">
                                          {m.name}
                                        </div>
                                        <div className="text-[10px] text-[var(--text-muted)]">
                                          {m.count} txns &middot; {share.toFixed(0)}%
                                        </div>
                                      </div>
                                      <span className="font-mono text-xs font-bold text-[var(--foreground)]">
                                        {autoFormatCurrency(m.total)}
                                      </span>
                                    </div>
                                    {/* Mini progress bar */}
                                    <div className="mt-2 h-1 overflow-hidden rounded-full bg-[var(--border-subtle)]">
                                      <div
                                        className="h-full rounded-full"
                                        style={{
                                          backgroundColor: getCategoryColor(cat.category),
                                          width: `${share}%`,
                                        }}
                                      />
                                    </div>
                                  </motion.button>
                                );
                              })}
                            </div>
                          </div>

                          {/* Merchant Deep Drill-down: exactly this merchant, in this category and
                            window (E2 B4) — its total equals the merchant card's. */}
                          {selectedMerchant && (
                            <motion.div
                              initial={{ opacity: 0, y: 12 }}
                              animate={{ opacity: 1, y: 0 }}
                              className="glass-card p-4"
                              data-role="merchant-drill"
                            >
                              {merchantDrill.loading ? (
                                <div className="flex justify-center py-4">
                                  <Loader2 className="h-5 w-5 animate-spin text-[var(--text-muted)]" />
                                </div>
                              ) : merchantDrill.error ? (
                                <ErrorState
                                  compact
                                  message={merchantDrill.error}
                                  onRetry={() => setMerchantAttempt((a) => a + 1)}
                                />
                              ) : merchantDetail ? (
                                <div>
                                  <div className="mb-4 flex items-center justify-between">
                                    <div className="flex items-center gap-3">
                                      <div className="flex h-8 w-8 items-center justify-center overflow-hidden rounded-lg bg-[var(--stat-bg)]">
                                        {guessDomain(selectedMerchant) ? (
                                          <img
                                            src={getMerchantLogo(guessDomain(selectedMerchant))!}
                                            alt=""
                                            className="h-5 w-5"
                                          />
                                        ) : (
                                          <Package className="h-4 w-4 text-[var(--text-muted)]" />
                                        )}
                                      </div>
                                      <div>
                                        <h4 className="text-sm font-bold text-[var(--foreground)]">
                                          {selectedMerchant}
                                        </h4>
                                        <div className="text-[10px] text-[var(--text-muted)]">
                                          {merchantDetail.count} transactions &middot; avg{" "}
                                          {autoFormatCurrency(merchantDetail.avg_transaction)}
                                          {merchantDetail.refunds > 0 &&
                                            ` · ${autoFormatCurrency(merchantDetail.refunds)} refunded`}
                                        </div>
                                      </div>
                                    </div>
                                    <div className="text-right">
                                      <div className="font-mono text-lg font-bold text-[var(--foreground)]">
                                        {autoFormatCurrency(merchantDetail.total)}
                                      </div>
                                      <div className="text-[10px] text-[var(--text-muted)]">
                                        {merchantDetail.range
                                          ? windowLabel(merchantDetail.range)
                                          : span
                                            ? windowLabel(span)
                                            : ""}{" "}
                                        &middot; {cat.category}
                                      </div>
                                    </div>
                                  </div>

                                  {/* Monthly trend mini chart (a range of several months) */}
                                  {merchantDetail.monthly?.length > 1 && (
                                    <div className="mb-4">
                                      <h5 className="mb-2 text-[10px] font-bold tracking-wider text-[var(--text-muted)] uppercase">
                                        Monthly Trend
                                      </h5>
                                      <div className="flex h-12 items-end gap-1">
                                        {merchantDetail.monthly.map((m, idx) => {
                                          const maxTotal = Math.max(
                                            ...merchantDetail.monthly.map((x) => x.total)
                                          );
                                          const height =
                                            maxTotal > 0
                                              ? (Math.max(0, m.total) / maxTotal) * 100
                                              : 0;
                                          return (
                                            <div
                                              key={idx}
                                              className="flex flex-1 flex-col items-center gap-0.5"
                                              title={`${monthLabel(m.month)}: ${autoFormatCurrency(m.total)}`}
                                            >
                                              <div
                                                className="w-full rounded-t"
                                                style={{
                                                  height: `${height}%`,
                                                  backgroundColor: getCategoryColor(cat.category),
                                                  minHeight: m.total > 0 ? "2px" : "0",
                                                }}
                                              />
                                              <span className="text-[8px] text-[var(--text-muted)]">
                                                {MONTH_SHORT[parseInt(m.month.split("-")[1]) - 1]}
                                              </span>
                                            </div>
                                          );
                                        })}
                                      </div>
                                    </div>
                                  )}

                                  {/* Transactions with enrichment (signed: refunds green, E2 M4) */}
                                  <h5 className="mb-2 text-[10px] font-bold tracking-wider text-[var(--text-muted)] uppercase">
                                    Transactions
                                    {merchantDetail.transactions.length > 15 &&
                                      ` (latest 15 of ${merchantDetail.transactions.length})`}
                                  </h5>
                                  <div className="max-h-48 space-y-1 overflow-y-auto">
                                    {merchantDetail.transactions.slice(0, 15).map((t, idx) => (
                                      <TxnRow
                                        key={t.id}
                                        txnId={t.id}
                                        index={idx}
                                        leading={
                                          <span className="w-14 flex-shrink-0 font-mono text-[11px] text-[var(--text-muted)]">
                                            {shortDate(t.date)}
                                          </span>
                                        }
                                        title={t.raw_description || t.merchant || ""}
                                        signedAmount={signedOf(t)}
                                        actions={
                                          <ChangeCategoryButton
                                            txnId={t.id}
                                            title={t.raw_description || t.merchant || "Unknown"}
                                            category={t.category}
                                            amount={signedOf(t)}
                                          />
                                        }
                                      >
                                        {t.enrichment?.restaurant && (
                                          <span className="block text-[10px] text-[var(--accent-amber)]">
                                            {t.enrichment.restaurant}
                                          </span>
                                        )}
                                        {t.enrichment?.products && (
                                          <span className="block text-[10px] text-[var(--accent-blue)]">
                                            {t.enrichment.products.join(", ")}
                                          </span>
                                        )}
                                        {t.enrichment?.items && (
                                          <span className="block text-[10px] text-[var(--accent-emerald)]">
                                            {t.enrichment.items
                                              .slice(0, 3)
                                              .map((item: any) => item.name)
                                              .join(", ")}
                                            {t.enrichment.items.length > 3 &&
                                              ` +${t.enrichment.items.length - 3} more`}
                                          </span>
                                        )}
                                      </TxnRow>
                                    ))}
                                  </div>
                                </div>
                              ) : null}
                            </motion.div>
                          )}

                          {/* Recent Transactions (when no merchant selected): the category's
                            newest rows from Ingest, signed (E2 M4). */}
                          {!selectedMerchant && (detail.recent_transactions?.length ?? 0) > 0 && (
                            <div>
                              <h4 className="mb-3 text-xs font-bold tracking-wider text-[var(--text-muted)] uppercase">
                                Recent Transactions
                              </h4>
                              <div className="space-y-1">
                                {detail.recent_transactions!.slice(0, 10).map((t, j) => (
                                  <TxnRow
                                    key={t.id}
                                    txnId={t.id}
                                    index={j}
                                    leading={
                                      <span className="w-14 flex-shrink-0 font-mono text-[11px] text-[var(--text-muted)]">
                                        {shortDate(t.date)}
                                      </span>
                                    }
                                    title={t.merchant || t.raw_description || "Unknown"}
                                    signedAmount={signedOf(t)}
                                    actions={
                                      <ChangeCategoryButton
                                        txnId={t.id}
                                        title={t.merchant || t.raw_description || "Unknown"}
                                        category={t.category}
                                        amount={signedOf(t)}
                                      />
                                    }
                                  />
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      ) : null}
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            </Fragment>
          );
        })}
      </div>
      {outflows.error ? (
        <div className="glass-card">
          <ErrorState
            compact
            message={`Outside Spending: ${outflows.error}`}
            onRetry={() => setOutflowAttempt((a) => a + 1)}
          />
        </div>
      ) : outflows.loaded && outflows.cards.length === 0 ? (
        <p
          className="text-center text-[11px] text-[var(--text-muted)]"
          data-role="outside-spending"
        >
          No money sent to people, invested or repaid on loans in this period.
        </p>
      ) : null}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Trends Tab
   ═══════════════════════════════════════════════════════════════════════════ */

function TrendsTab({ sel, dataSince }: { sel: Selection; dataSince: string | null }) {
  const today = new Date();
  const span = trendWindow(sel, today, dataSince);
  const { version } = useDataVersion();
  const [attempt, setAttempt] = useState(0);
  const trends = useJson<TrendsData>(trendsUrl(sel, today, dataSince), attempt, version);
  const data = trends.data;
  // null = "the default": the biggest four categories of the window (E2 C5)
  const [pickedCats, setPickedCats] = useState<Set<string> | null>(null);
  const visibleCats = useMemo(
    () => pickedCats ?? new Set(data ? topCategoriesByTotal(data.category_trends || {}, 4) : []),
    [pickedCats, data]
  );

  if (trends.loading)
    return (
      <div className="flex justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-[var(--text-muted)]" />
      </div>
    );
  if (trends.error || !data)
    return (
      <ErrorState
        message={trends.error ?? "No trend data"}
        onRetry={() => setAttempt((a) => a + 1)}
      />
    );

  const windowNote = span ? windowLabel(span) : "";

  const monthlyChart = data.monthly.map((m) => ({
    month: MONTH_SHORT[parseInt(m.month.split("-")[1]) - 1] + " " + m.month.split("-")[0].slice(2),
    total: m.total,
  }));

  const allMonths = data.monthly.map((m) => m.month);
  const catChartData = allMonths.map((month) => {
    const point: any = {
      month: MONTH_SHORT[parseInt(month.split("-")[1]) - 1] + " " + month.split("-")[0].slice(2),
    };
    data.categories.forEach((cat) => {
      point[cat] = data.category_trends[cat]?.[month] || 0;
    });
    return point;
  });

  // Ingest numbers weekdays 0 = Monday; the chart used to read 0 as Sunday (E2 B2).
  const weekdayChart = data.weekday_distribution.map((d) => ({
    day: ingestWeekdayLabel(d.dow),
    avg: Math.round(d.avg_spend),
  }));

  const toggleCat = (cat: string) => {
    setPickedCats(() => {
      const next = new Set(visibleCats);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  };

  const sectionNote = (
    <span className="ml-2 text-[10px] font-normal tracking-normal text-[var(--text-muted)] normal-case">
      {windowNote}
    </span>
  );

  return (
    <div className="space-y-6">
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass-card p-5"
      >
        <h3 className="mb-4 text-sm font-bold tracking-wider text-[var(--foreground)] uppercase">
          Monthly Spending Trend
          {sectionNote}
        </h3>
        <ResponsiveContainer width="100%" height={260}>
          <AreaChart data={monthlyChart}>
            <defs>
              <linearGradient id="spendGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--accent-blue)" stopOpacity={0.3} />
                <stop offset="100%" stopColor="var(--accent-blue)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" vertical={false} />
            <XAxis
              dataKey="month"
              tick={{ fontSize: 10, fill: "var(--text-muted)" }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tick={{ fontSize: 10, fill: "var(--text-muted)" }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v) =>
                v >= 100000
                  ? `${(v / 100000).toFixed(1)}L`
                  : v >= 1000
                    ? `${(v / 1000).toFixed(0)}k`
                    : v
              }
            />
            <RechartsTooltip content={<ChartTooltip />} />
            <Area
              type="monotone"
              dataKey="total"
              name="Total Spend"
              stroke="var(--accent-blue)"
              fill="url(#spendGrad)"
              strokeWidth={2.5}
              animationDuration={800}
              dot={{ r: 4, fill: "var(--accent-blue)", stroke: "var(--card)", strokeWidth: 2 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
        className="glass-card p-5"
      >
        <h3 className="mb-3 text-sm font-bold tracking-wider text-[var(--foreground)] uppercase">
          Category Trends
          {sectionNote}
        </h3>
        <div className="mb-4 flex flex-wrap gap-2">
          {data.categories.map((cat, i) => (
            <button
              key={cat}
              onClick={() => toggleCat(cat)}
              aria-pressed={visibleCats.has(cat)}
              className={`rounded-full border px-2.5 py-1 text-[10px] font-medium transition-all ${
                visibleCats.has(cat)
                  ? "border-transparent text-white"
                  : "border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--foreground)]"
              }`}
              style={
                visibleCats.has(cat)
                  ? { backgroundColor: CHART_COLORS[i % CHART_COLORS.length] }
                  : {}
              }
            >
              {cat}
            </button>
          ))}
        </div>
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={catChartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" vertical={false} />
            <XAxis
              dataKey="month"
              tick={{ fontSize: 10, fill: "var(--text-muted)" }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tick={{ fontSize: 10, fill: "var(--text-muted)" }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v) =>
                v >= 100000
                  ? `${(v / 100000).toFixed(1)}L`
                  : v >= 1000
                    ? `${(v / 1000).toFixed(0)}k`
                    : v
              }
            />
            <RechartsTooltip content={<ChartTooltip />} />
            {data.categories.map(
              (cat, i) =>
                visibleCats.has(cat) && (
                  <Line
                    key={cat}
                    type="monotone"
                    dataKey={cat}
                    name={cat}
                    stroke={CHART_COLORS[i % CHART_COLORS.length]}
                    strokeWidth={2}
                    dot={false}
                    activeDot={{ r: 4 }}
                    animationDuration={600}
                  />
                )
            )}
          </LineChart>
        </ResponsiveContainer>
      </motion.div>

      <div className="grid grid-cols-1 gap-4">
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15 }}
          className="glass-card p-5"
        >
          <h3 className="mb-4 text-sm font-bold tracking-wider text-[var(--foreground)] uppercase">
            Day-of-Week Pattern
            {sectionNote}
          </h3>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={weekdayChart} barSize={28}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" vertical={false} />
              <XAxis
                dataKey="day"
                tick={{ fontSize: 10, fill: "var(--text-muted)" }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tick={{ fontSize: 10, fill: "var(--text-muted)" }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v) => (v >= 1000 ? `${(v / 1000).toFixed(0)}k` : v)}
              />
              <RechartsTooltip content={<ChartTooltip />} />
              <Bar
                dataKey="avg"
                name="Avg Spend"
                fill="var(--accent-blue)"
                radius={[4, 4, 0, 0]}
                animationDuration={600}
              />
            </BarChart>
          </ResponsiveContainer>
        </motion.div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Food & Dining Tab — Restaurant-level drill-down
   ═══════════════════════════════════════════════════════════════════════════ */

function FoodTab({ sel, dataSince }: { sel: Selection; dataSince: string | null }) {
  const today = new Date();
  const span = selectionWindow(sel, today, dataSince);
  const [attempt, setAttempt] = useState(0);
  // The selection's window is sent to Ingest (E1); the tab used to load every order once and
  // filter by month in the browser, so the range pills could not reach it.
  const food = useJson<{ orders: any[] }>(foodOrdersUrl(sel, today, dataSince), attempt);
  const [selected, setSelected] = useState<string | null>(null);
  const [sortMode, setSortMode] = useState<FoodSort>("total");
  const [query, setQuery] = useState("");

  if (food.loading)
    return (
      <div className="flex justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-[var(--text-muted)]" />
      </div>
    );
  if (food.error)
    return <ErrorState message={food.error} onRetry={() => setAttempt((a) => a + 1)} />;

  const orders = food.data?.orders ?? [];
  if (!orders.length)
    return (
      <div className="py-20 text-center text-sm text-[var(--text-muted)]">
        No food orders found for {span ? windowLabel(span) : "this period"}
      </div>
    );

  // The headline cards come from every order, spend-sorted; search and sort only shape the
  // grid (E2 M10).
  const {
    total,
    orderCount,
    top,
    restaurantCount,
    grid: restaurants,
  } = summarizeFood(orders, query, sortMode);

  const platformDomain = (p: string | undefined) => (p === "swiggy" ? "swiggy.in" : null);

  return (
    <div className="space-y-6">
      {/* Summary stats */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <GradientStatCard
          icon={Utensils}
          title="Total Food Spend"
          gradientKey="red"
          delay={0}
          value={total}
          sub={`${orderCount} orders`}
        />
        <GradientStatCard
          icon={Hash}
          title="Orders"
          gradientKey="amber"
          delay={1}
          value={orderCount}
          isCurrency={false}
          sub={`avg ${autoFormatCurrency(total / Math.max(1, orderCount))} per order`}
        />
        <GradientStatCard
          icon={Flame}
          title="Top Restaurant"
          gradientKey="purple"
          delay={2}
          value={top?.total || 0}
          displayText={top?.name || "—"}
          sub={`${top?.count || 0} orders`}
        />
        <GradientStatCard
          icon={Activity}
          title="Restaurants"
          gradientKey="cyan"
          delay={3}
          value={restaurantCount}
          isCurrency={false}
          sub="unique places ordered from"
        />
      </div>

      {/* Restaurant cards */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2 }}
        className="glass-card p-5"
      >
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <h3 className="text-sm font-bold tracking-wider text-[var(--foreground)] uppercase">
            Restaurants &amp; Orders
            {span && (
              <span className="ml-2 text-[10px] font-normal tracking-normal text-[var(--text-muted)] normal-case">
                {windowLabel(span)}
              </span>
            )}
          </h3>
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search restaurants..."
              aria-label="Search restaurants"
              className="w-40 rounded-lg border border-[var(--border-subtle)] bg-[var(--input)] px-3 py-1.5 text-xs text-[var(--foreground)] placeholder:text-[var(--text-muted)] focus:border-[var(--accent-blue)] focus:ring-1 focus:ring-[var(--accent-blue)] focus:outline-none"
            />
            <div className="flex items-center rounded-lg border border-[var(--border-subtle)] bg-[var(--stat-bg)] p-0.5">
              {(["total", "count", "avg"] as FoodSort[]).map((m) => (
                <button
                  key={m}
                  onClick={() => setSortMode(m)}
                  aria-pressed={sortMode === m}
                  className={`rounded-md px-2 py-1 text-[10px] font-semibold transition-all ${
                    sortMode === m
                      ? "bg-[var(--accent-blue)] text-white"
                      : "text-[var(--text-muted)] hover:text-[var(--foreground)]"
                  }`}
                >
                  {m === "total" ? "Spend" : m === "count" ? "Orders" : "Avg"}
                </button>
              ))}
            </div>
          </div>
        </div>
        {restaurants.length === 0 && query ? (
          <div className="py-8 text-center text-xs text-[var(--text-muted)]">
            No restaurants match &ldquo;{query}&rdquo;
          </div>
        ) : null}
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3">
          {restaurants.map((r, i) => {
            const pct = total > 0 ? (r.total / total) * 100 : 0;
            const isSelected = selected === r.name;
            const domain = platformDomain(r.platform);
            return (
              <motion.div
                key={r.name}
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: Math.min(i, 20) * 0.03 }}
                className={`cursor-pointer rounded-xl border p-3 transition-all ${isSelected ? "col-span-1 border-[var(--accent-blue)]/40 bg-[var(--accent-blue)]/5 md:col-span-2 lg:col-span-3" : "border-[var(--border-subtle)] bg-[var(--stat-bg)] hover:border-[var(--border)]"}`}
                onClick={() => setSelected(isSelected ? null : r.name)}
              >
                <div className="flex items-center gap-3">
                  <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--card)]">
                    {domain ? (
                      <img src={getMerchantLogo(domain)!} alt="" className="h-5 w-5" />
                    ) : (
                      <Utensils className="h-4 w-4 text-[var(--text-muted)]" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-semibold text-[var(--foreground)]">
                      {r.name}
                    </div>
                    <div className="text-[10px] text-[var(--text-muted)]">
                      {r.count} orders &middot; {pct.toFixed(0)}% &middot; avg{" "}
                      {autoFormatCurrency(r.total / Math.max(1, r.count))}
                    </div>
                  </div>
                  <span className="font-mono text-xs font-bold text-[var(--foreground)]">
                    {autoFormatCurrency(r.total)}
                  </span>
                </div>
                <div className="mt-2 h-1 overflow-hidden rounded-full bg-[var(--border-subtle)]">
                  <div
                    className="h-full rounded-full bg-orange-500/60"
                    style={{ width: `${pct}%` }}
                  />
                </div>

                {/* Expanded: this restaurant's orders + items */}
                {isSelected && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    className="mt-4 space-y-2 border-t border-[var(--border-subtle)] pt-4"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {r.orders
                      .slice()
                      .sort((a, b) => (b.order_date || "").localeCompare(a.order_date || ""))
                      .map((o: any, idx: number) => (
                        <div
                          key={o.order_id || idx}
                          className="rounded-lg bg-[var(--card)] px-3 py-2"
                        >
                          <div className="flex items-center justify-between">
                            <span className="font-mono text-[11px] text-[var(--text-muted)]">
                              {o.order_date ? shortDate(o.order_date, true) : ""}
                              {o.platform === "toing" ? " · Toing" : ""}
                            </span>
                            <span className="font-mono text-xs font-semibold text-[var(--foreground)]">
                              {autoFormatCurrency(o.total_amount)}
                            </span>
                          </div>
                          {o.items?.length > 0 && (
                            <div className="mt-1.5 space-y-0.5">
                              {o.items.map((it: any, j: number) => (
                                <div
                                  key={j}
                                  className="flex items-center justify-between text-[11px]"
                                >
                                  <span className="text-[var(--foreground-secondary)]">
                                    {it.qty > 1 ? `${it.qty}× ` : ""}
                                    {it.name}
                                  </span>
                                  {typeof it.price === "number" && (
                                    <span className="font-mono text-[var(--text-muted)]">
                                      {autoFormatCurrency(it.price)}
                                    </span>
                                  )}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                  </motion.div>
                )}
              </motion.div>
            );
          })}
        </div>
      </motion.div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Main Component
   ═══════════════════════════════════════════════════════════════════════════ */

export default function ExpenseTracker() {
  // E3: one data version for the whole page — a re-filed transaction or a removed payee rule
  // refreshes every block in place.
  return (
    <DataVersionProvider>
      <ExpensePage />
    </DataVersionProvider>
  );
}

function ExpensePage() {
  const { version } = useDataVersion();
  const [tab, setTab] = useState<TabId>("overview");
  // period null = not known yet: the first request lets the server pick the latest month that has
  // data, and the page then shows that month (E2 M2 — it used to send this calendar month, so a
  // stale feed opened on an empty month with no hint).
  const [sel, setSel] = useState<Selection>({ mode: "month", period: null });
  const [dataSince, setDataSince] = useState<string | null>(null);
  const [dataUntil, setDataUntil] = useState<string | null>(null);
  const [focusCategory, setFocusCategory] = useState<string | null>(null);
  const [focusOutflows, setFocusOutflows] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [dash, setDash] = useState<{
    key: string;
    /** The selection + attempt the data is for (the key without the data version). */
    base: string;
    overview: OverviewData | null;
    recurring: RecurringItem[];
    error: string | null;
  } | null>(null);
  // The key of the data in `dash` — mirrored in a ref so the effect can skip a fetch for data it
  // already holds (the opening month, resolved from the first response) without depending on it.
  const heldKey = useRef<string | null>(null);

  const today = new Date();
  const dashUrl = dashboardUrl(sel, today, dataSince);
  const base = `${dashUrl}#${attempt}`;
  const key = versionedKey(base, version);
  // A new data version alone (E3) refetches in place: the held numbers stay until the new ones land.
  const loading = !dash || (dash.key !== key && (dash.base !== base || !!dash.error));

  useEffect(() => {
    if (heldKey.current === key) return; // already holding this data (the resolved opening month)
    let cancelled = false;
    getJson(dashUrl)
      .then((d) => {
        if (cancelled) return;
        const overview = (d.overview || null) as OverviewData | null;
        const recurring = d.recurring?.recurring || [];
        setDataSince(overview?.data_since ?? null);
        setDataUntil(overview?.data_until ?? null);
        if (sel.mode === "month" && !sel.period && d.period) {
          // Show the month the server picked, without fetching it a second time.
          const resolved: Selection = { mode: "month", period: d.period };
          const b = `${dashboardUrl(resolved, new Date(), overview?.data_since)}#${attempt}`;
          const k = versionedKey(b, version);
          heldKey.current = k;
          setSel(resolved);
          setDash({ key: k, base: b, overview, recurring, error: null });
        } else {
          heldKey.current = key;
          setDash({ key, base, overview, recurring, error: null });
        }
      })
      .catch((e) => {
        if (cancelled) return;
        heldKey.current = key;
        setDash({ key, base, overview: null, recurring: [], error: errorMessage(e) });
      });
    return () => {
      cancelled = true;
    };
    // `sel`, `attempt` and `version` are part of `dashUrl` / `key`
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dashUrl, key]);

  const span = selectionWindow(sel, today, dataSince);
  const hmSpan = heatmapWindow(sel, today, dataSince);
  const [heatAttempt, setHeatAttempt] = useState(0);
  const heat = useJson<{ heatmap?: HeatmapDay[]; days?: HeatmapDay[] }>(
    hmSpan ? heatmapUrl(sel, today, dataSince) : null,
    heatAttempt,
    version
  );

  const changeMode = (mode: RangeMode) => setSel((s) => ({ ...s, mode }));
  const openMonth = (period: string) => setSel({ mode: "month", period });
  const openCategory = (category: string) => {
    setFocusCategory(category);
    setFocusOutflows(false);
    setTab("categories");
  };
  const openOutflows = () => {
    setFocusCategory(null);
    setFocusOutflows(true);
    setTab("categories");
  };

  return (
    <div className="relative mx-auto max-w-[1400px]">
      {/* Dot pattern background */}
      <div
        className="pointer-events-none fixed inset-0 z-0 opacity-[0.03]"
        style={{
          backgroundImage: "radial-gradient(circle, var(--foreground) 1px, transparent 1px)",
          backgroundSize: "24px 24px",
        }}
      />

      <div className="relative z-10">
        {/* Header */}
        <div className="mb-6 flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center">
          <div>
            <h1 className="text-2xl font-extrabold text-[var(--foreground)]">Expenses</h1>
            <p className="mt-0.5 text-xs text-[var(--text-muted)]">Track where your money goes</p>
          </div>
          <TimeRangeSelector
            period={sel.period}
            onPeriodChange={openMonth}
            rangeMode={sel.mode}
            onRangeModeChange={changeMode}
            months={monthOptions(today, [sel.period])}
            activeRange={sel.mode !== "month" && span ? windowLabel(span) : null}
            staleNote={staleDataNote(dataUntil, today)}
          />
        </div>

        {/* Tab Bar */}
        <div className="mb-6 flex items-center gap-1 overflow-x-auto pb-1" role="tablist">
          {TABS.map((t) => {
            const Icon = t.icon;
            const active = tab === t.id;
            return (
              <button
                key={t.id}
                role="tab"
                aria-selected={active}
                onClick={() => {
                  setFocusCategory(null);
                  setFocusOutflows(false);
                  setTab(t.id);
                }}
                className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium whitespace-nowrap transition-all ${
                  active
                    ? "border border-[var(--accent-blue)]/20 bg-[var(--accent-blue)]/10 text-[var(--accent-blue)]"
                    : "text-[var(--text-muted)] hover:bg-[var(--card-hover)] hover:text-[var(--foreground)]"
                }`}
              >
                <Icon className="h-4 w-4" />
                {t.label}
              </button>
            );
          })}
        </div>

        {/* Tab Content */}
        {dash?.error && dash.key === key ? (
          <ErrorState message={dash.error} onRetry={() => setAttempt((a) => a + 1)} />
        ) : loading ? (
          <div className="flex flex-col items-center justify-center gap-3 py-32">
            <Loader2 className="h-8 w-8 animate-spin text-[var(--accent-blue)]" />
            <span className="text-sm text-[var(--text-muted)]">Loading expense data...</span>
          </div>
        ) : (
          <>
            {tab === "overview" && dash?.overview && span && hmSpan && (
              <OverviewTab
                data={dash.overview}
                heatmap={heat.data?.heatmap || heat.data?.days || []}
                heatmapSpan={hmSpan}
                heatmapError={heat.error}
                onRetryHeatmap={() => setHeatAttempt((a) => a + 1)}
                recurring={dash.recurring}
                sel={sel}
                span={span}
                dataSince={dataSince}
                onOpenCategory={openCategory}
                onSelectMonth={openMonth}
                onOpenOutflows={openOutflows}
              />
            )}
            {tab === "categories" && (
              <CategoriesTab
                key={focusCategory ?? (focusOutflows ? "#outside-spending" : "")}
                sel={sel}
                dataSince={dataSince}
                focusCategory={focusCategory}
                focusOutflows={focusOutflows}
              />
            )}
            {tab === "food" && <FoodTab sel={sel} dataSince={dataSince} />}
            {tab === "trends" && <TrendsTab sel={sel} dataSince={dataSince} />}
          </>
        )}
      </div>
    </div>
  );
}
