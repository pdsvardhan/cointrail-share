import { describe, it, expect } from "vitest";
import { comparisonLabel, overviewHeadline, type OverviewFields } from "@/lib/expense-overview";

/**
 * iter-18 H5b / M13 (brief contract 4). Numbers follow the 2026-10-19 audit's
 * October-to-the-18th example: the dashboard showed ₹29,018 and "-79.7% vs last month"
 * (18 days of October against all of September), most of it money sent to people.
 */

const legacy: OverviewFields = {
  total: 29018,
  txn_count: 64,
  daily_avg: 2072.71, // old Ingest: divided by 14 active days
  prev_month_total: 143112,
};

const contract: OverviewFields = {
  total: 10593, // everyday spend, net of refunds
  txn_count: 41,
  daily_avg: 588.5, // 10593 / 18 calendar days
  prev_month_total: 62807,
  gross_spend: 11892,
  refunds: 1299,
  fees_interest: 0,
  outflows: {
    sent_to_people: { total: 18425, count: 9 },
    investments: { total: 0, count: 0 },
  },
  days_elapsed: 18,
  prev_same_period_total: 12000,
};

describe("comparisonLabel", () => {
  it("compares with the same days of last month when Ingest provides them", () => {
    expect(comparisonLabel(contract)).toBe("-11.7% vs same days last month");
  });

  it("falls back to the full previous month on an older Ingest", () => {
    expect(comparisonLabel(legacy)).toBe("-79.7% vs last month");
  });

  it("says so when there is nothing to compare against", () => {
    expect(comparisonLabel({ ...contract, prev_same_period_total: 0 })).toBe(
      "No spend in the same days last month to compare"
    );
    expect(comparisonLabel({ total: 500, txn_count: 1, daily_avg: 500 })).toBe(
      "No spend in the last month to compare"
    );
  });

  it("a 3M / 6M / 1Y / All range compares with the window of equal length before it (iteration 203 E1)", () => {
    const range: OverviewFields = {
      ...contract,
      total: 8049,
      prev_same_period_total: 1100,
      comparison_basis: "previous_window",
      comparison_window: { from: "2026-05-12", to: "2026-07-31", days: 81 },
    };
    expect(comparisonLabel(range)).toBe("+631.7% vs previous 81 days");
    expect(comparisonLabel({ ...range, prev_same_period_total: 0 })).toBe(
      "No spend in the previous 81 days to compare"
    );
    expect(comparisonLabel({ ...contract, comparison_basis: "previous_month" })).toBe(
      "-11.7% vs last month"
    );
  });

  it("reports an unchanged total as the same", () => {
    expect(comparisonLabel({ ...contract, prev_same_period_total: 10593 })).toBe(
      "Same as the same days last month"
    );
    expect(comparisonLabel({ ...contract, prev_same_period_total: 9000 })).toBe(
      "+17.7% vs same days last month"
    );
  });
});

describe("overviewHeadline", () => {
  it("headline Spending is the everyday total with refunds netted", () => {
    const h = overviewHeadline(contract);
    expect(h.spending).toBe(10593);
    expect(h.refunds).toBe(1299);
  });

  it("surfaces money sent to people and investments without mixing them into spending", () => {
    const h = overviewHeadline(contract);
    expect(h.sentToPeople).toEqual({ total: 18425, count: 9 });
    expect(h.investments).toEqual({ total: 0, count: 0 });
    expect(h.spending).toBe(contract.total);
  });

  it("daily average is Ingest's calendar-day average, labelled with the day count", () => {
    const h = overviewHeadline(contract);
    expect(h.dailyAvg).toBe(588.5);
    expect(h.dailyAvgNote).toBe("41 transactions over 18 days");
    expect(overviewHeadline({ ...contract, days_elapsed: 1, txn_count: 1 }).dailyAvgNote).toBe(
      "1 transaction over 1 day"
    );
  });

  it("keeps working when the contract fields are absent", () => {
    const h = overviewHeadline(legacy);
    expect(h.spending).toBe(29018);
    expect(h.refunds).toBe(0);
    expect(h.sentToPeople).toBeNull();
    expect(h.investments).toBeNull();
    expect(h.dailyAvg).toBe(2072.71);
    expect(h.dailyAvgNote).toBe("64 transactions");
    expect(h.comparison).toBe("-79.7% vs last month");
  });

  it("ignores malformed outflow buckets and non-positive refunds", () => {
    const h = overviewHeadline({
      ...contract,
      refunds: -50,
      outflows: { sent_to_people: { count: 3 }, investments: null },
    });
    expect(h.refunds).toBe(0);
    expect(h.sentToPeople).toBeNull();
    expect(h.investments).toBeNull();
  });

  it("shows loan repayments beside Spending only in a period that has them", () => {
    const withLoan = overviewHeadline({
      ...contract,
      outflows: { ...contract.outflows, loans: { total: 122057, count: 2 } },
    });
    expect(withLoan.loans).toEqual({ total: 122057, count: 2 });
    expect(withLoan.spending).toBe(contract.total);
    const none = overviewHeadline({
      ...contract,
      outflows: { ...contract.outflows, loans: { total: 0, count: 0 } },
    });
    expect(none.loans).toBeNull();
    expect(overviewHeadline(legacy).loans).toBeNull();
  });
});
