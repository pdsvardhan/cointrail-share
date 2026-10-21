import { describe, it, expect, beforeAll, afterAll } from "vitest";
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
  monthBounds,
  monthOptions,
  rangeForMode,
  selectionWindow,
  spendBars,
  staleDataNote,
  topCategoriesByTotal,
  trendsUrl,
  trendWindow,
  windowLabel,
  type Selection,
} from "@/lib/expense-range";

/**
 * Iteration 203 E1 / E2: the 3M / 6M / 1Y / All pills used to change nothing but the page's
 * look. Every request the Expenses page makes is built from the selection by these helpers.
 * Runs in Asia/Kolkata like the owner's browser; "today" is 20 Oct 2026.
 */

const originalTZ = process.env.TZ;
beforeAll(() => {
  process.env.TZ = "Asia/Kolkata";
});
afterAll(() => {
  if (originalTZ === undefined) delete process.env.TZ;
  else process.env.TZ = originalTZ;
});

const TODAY = () => new Date(2026, 9, 20, 23, 30); // 20 Oct 2026, late evening IST
const SINCE = "2025-02-03";
const month = (period: string | null): Selection => ({ mode: "month", period });

describe("range pills -> date windows (E1 AC2)", () => {
  it("3M / 6M / 1Y start on the 1st of the month 2 / 5 / 11 months back and end today", () => {
    expect(rangeForMode("3M", TODAY())).toEqual({ from: "2026-08-01", to: "2026-10-20" });
    expect(rangeForMode("6M", TODAY())).toEqual({ from: "2026-05-01", to: "2026-10-20" });
    expect(rangeForMode("1Y", TODAY())).toEqual({ from: "2025-11-01", to: "2026-10-20" });
  });

  it("All starts at the first transaction (fallback when unknown)", () => {
    expect(rangeForMode("all", TODAY(), SINCE)).toEqual({ from: SINCE, to: "2026-10-20" });
    expect(rangeForMode("all", TODAY(), null)).toEqual({ from: "2020-01-01", to: "2026-10-20" });
  });

  it("month mode is the picked month; no window before the first month is known", () => {
    expect(rangeForMode("month", TODAY())).toBeNull();
    expect(selectionWindow(month("2026-09"), TODAY())).toEqual(monthBounds("2026-09"));
    expect(monthBounds("2026-02")).toEqual({ from: "2026-02-01", to: "2026-02-28" });
    expect(selectionWindow(month(null), TODAY())).toBeNull();
  });

  it("labels", () => {
    expect(windowLabel({ from: "2026-08-01", to: "2026-10-20" })).toBe("1 Aug – 20 Oct 2026");
    expect(windowLabel({ from: "2025-11-01", to: "2026-10-20" })).toBe("1 Nov 2025 – 20 Oct 2026");
    expect(windowLabel(monthBounds("2026-09"))).toBe("Sep 2026");
  });
});

describe("every tab's request carries the selection (E1 AC2 / AC4)", () => {
  const range: Selection = { mode: "3M", period: "2026-10" };

  it("dashboard: no period on first load (server picks the latest month, M2), then the month or range", () => {
    expect(dashboardUrl(month(null), TODAY())).toBe("/api/expenses/dashboard");
    expect(dashboardUrl(month("2026-09"), TODAY())).toBe("/api/expenses/dashboard?period=2026-09");
    expect(dashboardUrl(range, TODAY())).toBe(
      "/api/expenses/dashboard?from=2026-08-01&to=2026-10-20"
    );
  });

  it("categories and the category drill", () => {
    expect(categoriesUrl(range, TODAY())).toBe(
      "/api/expenses/dashboard/categories?from=2026-08-01&to=2026-10-20"
    );
    expect(categoriesUrl(month("2026-09"), TODAY(), null, "Food & Dining")).toBe(
      "/api/expenses/dashboard/categories?period=2026-09&category=Food%20%26%20Dining"
    );
  });

  it("merchant drill: exact merchant, its category and the same window (B4)", () => {
    expect(merchantDetailUrl(range, TODAY(), null, "Swiggy", "Food & Dining")).toBe(
      "/api/expenses/dashboard/merchant-detail?merchant=Swiggy&category=Food%20%26%20Dining&from=2026-08-01&to=2026-10-20"
    );
    expect(merchantDetailUrl(month("2026-09"), TODAY(), null, "Swiggy", "Food & Dining")).toContain(
      "from=2026-09-01&to=2026-09-30"
    );
  });

  it("food orders, View All, trends and heatmap", () => {
    expect(foodOrdersUrl(range, TODAY())).toBe(
      "/api/food/orders?from=2026-08-01&to=2026-10-20&limit=2000"
    );
    expect(foodOrdersUrl(month("2026-09"), TODAY())).toBe(
      "/api/food/orders?from=2026-09-01&to=2026-09-30&limit=2000"
    );
    expect(allTransactionsUrl(range, TODAY(), null, "amount", 50)).toBe(
      "/api/expenses/dashboard/transactions?from=2026-08-01&to=2026-10-20&sort=amount&offset=50&limit=50"
    );
    expect(trendsUrl(range, TODAY())).toBe(
      "/api/expenses/dashboard/trends?from=2026-08-01&to=2026-10-20"
    );
    expect(heatmapUrl(range, TODAY())).toBe(
      "/api/expenses/dashboard/heatmap?from=2026-08-01&to=2026-10-20"
    );
  });

  it("a single month: heatmap shows the 6 months and trends the 12 months ending with it (C2)", () => {
    // a month picked 9 months back is on the heatmap now
    expect(heatmapWindow(month("2026-01"), TODAY())).toEqual({
      from: "2025-08-01",
      to: "2026-01-31",
    });
    expect(trendWindow(month("2026-10"), TODAY())).toEqual({
      from: "2025-11-01",
      to: "2026-10-20",
    });
    expect(heatmapUrl(month(null), TODAY())).toBe("/api/expenses/dashboard/heatmap");
  });
});

describe("the spending chart over a window (E1 AC3)", () => {
  const daily = [
    { date: "2026-07-31", total: 99 },
    { date: "2026-08-10", total: 500 },
    { date: "2026-08-20", total: 1000 },
    { date: "2026-09-05", total: 1000 },
    { date: "2026-09-06", total: -300 },
    { date: "2026-10-03", total: 1200 },
  ];

  it("a month is one bar per day labelled with the day", () => {
    const bars = spendBars(daily, monthBounds("2026-08"));
    expect(bars.map((b) => [b.kind, b.label, b.total])).toEqual([
      ["day", "10", 500],
      ["day", "20", 1000],
    ]);
  });

  it("a multi-month range is one bar per calendar month, every month present", () => {
    const bars = spendBars(daily, { from: "2026-07-01", to: "2026-11-20" });
    expect(bars.map((b) => [b.key, b.label, b.total])).toEqual([
      ["2026-07", "Jul 26", 99],
      ["2026-08", "Aug 26", 1500],
      ["2026-09", "Sep 26", 700],
      ["2026-10", "Oct 26", 1200],
      ["2026-11", "Nov 26", 0],
    ]);
    expect(bars.every((b) => b.kind === "month")).toBe(true);
  });

  it("a short window across a month boundary labels days with their month", () => {
    const bars = spendBars(daily, { from: "2026-08-15", to: "2026-09-10" });
    expect(bars.map((b) => b.label)).toEqual(["20 Aug", "5 Sep", "6 Sep"]);
  });
});

describe("chart details (E2 B2 / C5 / M5)", () => {
  it("weekday bars use Ingest's numbering, 0 = Monday (B2)", () => {
    // 19 Oct 2026 is a Monday; Python's date.weekday() gives 0 for it.
    expect(new Date(2026, 9, 19).getDay()).toBe(1);
    expect(ingestWeekdayLabel(0)).toBe("Mon");
    expect(ingestWeekdayLabel(6)).toBe("Sun");
    expect([0, 1, 2, 3, 4, 5, 6].map(ingestWeekdayLabel)).toEqual([
      "Mon",
      "Tue",
      "Wed",
      "Thu",
      "Fri",
      "Sat",
      "Sun",
    ]);
  });

  it("trend pills start with the biggest categories, not the first alphabetically (C5)", () => {
    const trends = {
      "ATM Withdrawal": { "2026-09": 100 },
      "Bank Charges": { "2026-09": 50 },
      EMI: { "2026-09": 300, "2026-10": 300 },
      "Food & Dining": { "2026-09": 5000, "2026-10": 4000 },
      Shopping: { "2026-10": 9500 },
      Travel: { "2026-09": 2000 },
    };
    expect(topCategoriesByTotal(trends, 4)).toEqual(["Shopping", "Food & Dining", "Travel", "EMI"]);
  });

  it("summary bars scale to the largest month, not the newest (M5)", () => {
    const totals = [10000, 40000, 20000]; // newest (partial) month first
    expect(barWidthPct(10000, totals)).toBe(25);
    expect(barWidthPct(40000, totals)).toBe(100);
    expect(barWidthPct(0, totals)).toBe(0);
    expect(barWidthPct(5, [0])).toBe(0);
  });
});

describe("opening month (E2 M2)", () => {
  it("says when the newest data is not from this month", () => {
    expect(staleDataNote("2026-09-28", TODAY())).toBe("Latest data: 28 Sep 2026");
    expect(staleDataNote("2026-10-18", TODAY())).toBeNull();
    expect(staleDataNote(null, TODAY())).toBeNull();
  });

  it("the picker always offers the month being shown", () => {
    const opts = monthOptions(TODAY(), ["2025-06"]);
    expect(opts[0]).toBe("2026-10");
    expect(opts).toContain("2025-11");
    expect(opts).toContain("2025-06");
    expect(opts).toHaveLength(13);
  });
});
