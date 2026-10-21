import { describe, it, expect } from "vitest";
import { cadenceLabel, frequencyLabel, monthlyEquiv, totalMonthly } from "@/lib/recurring";

/**
 * Iteration 203 E2 M6: the Overview's "Recurring Payments /month" and the Recurring page's
 * "Total Monthly" use one monthly-equivalent, driven by Ingest's `frequency`.
 */

const monthly = {
  type: "subscription",
  frequency: "monthly",
  avg_amount: 649,
  months: ["2026-09", "2026-10"],
};
// A yearly plan charged in two different years: the old Overview rule (months.length === 1)
// counted it at full price every month and labelled it "/mo".
const yearly = {
  type: "subscription",
  frequency: "yearly",
  avg_amount: 1499,
  months: ["2025-04", "2026-04"],
};
const biennial = {
  type: "subscription",
  frequency: "biennial",
  avg_amount: 7200,
  months: ["2025-01"],
};
const emi = { type: "emi", frequency: null, avg_amount: 4321.09, months: ["2026-08", "2026-09"] };
const olderIngest = { type: "subscription", avg_amount: 1200, months: ["2026-03"] };

describe("monthly equivalent (M6)", () => {
  it("follows Ingest's frequency", () => {
    expect(monthlyEquiv(monthly)).toBe(649);
    expect(monthlyEquiv(yearly)).toBeCloseTo(1499 / 12, 6);
    expect(monthlyEquiv(biennial)).toBeCloseTo(7200 / 24, 6);
    expect(monthlyEquiv(emi)).toBe(4321.09);
  });

  it("labels match", () => {
    expect([monthly, yearly, biennial, emi].map(frequencyLabel)).toEqual([
      "/mo",
      "/yr",
      "/2yr",
      "/mo",
    ]);
    expect(cadenceLabel(biennial)).toBe("every 2 years");
  });

  it("falls back to the one-month hint only without a frequency", () => {
    expect(monthlyEquiv(olderIngest)).toBe(100);
    expect(frequencyLabel(olderIngest)).toBe("/yr");
  });

  it("one total for both pages", () => {
    expect(totalMonthly([monthly, yearly, biennial, emi])).toBeCloseTo(
      649 + 1499 / 12 + 7200 / 24 + 4321.09,
      6
    );
  });
});
