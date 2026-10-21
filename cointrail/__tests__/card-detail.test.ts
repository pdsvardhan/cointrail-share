import { describe, it, expect } from "vitest";
import {
  netRefundNote,
  sameBillNote,
  splitCategoryBreakdown,
  unbilledSummary,
} from "@/lib/card-detail";

/**
 * Iteration 203 E2, verifier round 2 polish on the card page: the add-on note named the wrong card,
 * a category holding only refunds showed as negative spend, and the Unbilled tile's count left out
 * the refund the Unbilled list shows.
 */

describe("sameBillNote", () => {
  it("names the other cards on the bill without calling them add-ons (the primary is one too)", () => {
    expect(sameBillNote(["4321"])).toBe("incl. card 4321 (same bill)");
    expect(sameBillNote(["4321", "8765"])).toBe("incl. cards 4321, 8765 (same bill)");
  });

  it("is null for a card billed alone", () => {
    expect(sameBillNote([])).toBeNull();
  });
});

describe("splitCategoryBreakdown", () => {
  it("keeps spending rows and moves a refunds-only category out, as a positive refund", () => {
    const rows = [
      { category: "Food & Dining", total: 3200, count: 5, merchants: [] },
      { category: "Pass-Through", total: -1466, count: 2, merchants: [] },
      { category: "Shopping", total: 900, count: 1, merchants: [] },
    ];
    const { spend, refundsOnly } = splitCategoryBreakdown(rows);
    expect(spend.map((r) => r.category)).toEqual(["Food & Dining", "Shopping"]);
    expect(refundsOnly).toEqual([{ category: "Pass-Through", refunded: 1466, count: 2 }]);
  });

  it("labels a net-refund row without claiming there were no purchases (refunds may outweigh them)", () => {
    const { refundsOnly } = splitCategoryBreakdown([
      { category: "Uncategorized", total: -1640.77, count: 4, merchants: [] },
    ]);
    expect(refundsOnly).toEqual([{ category: "Uncategorized", refunded: 1640.77, count: 4 }]);
    expect(netRefundNote()).toBe("(refunds exceeded purchases this period)");
    expect(netRefundNote()).not.toMatch(/no .* spending/);
  });

  it("drops zero rows", () => {
    const { spend, refundsOnly } = splitCategoryBreakdown([
      { category: "Fuel", total: 0, count: 0, merchants: [] },
    ]);
    expect(spend).toEqual([]);
    expect(refundsOnly).toEqual([]);
  });
});

describe("unbilledSummary", () => {
  it("counts spends and refunds apart, so the tile agrees with the Unbilled list", () => {
    const s = unbilledSummary([
      { unbilled: { total: 8258, count: 6, credits: { total: 2, count: 1 } } },
    ]);
    expect(s).toMatchObject({ total: 8258, count: 6, credits: { total: 2, count: 1 } });
    expect(s.text).toBe("6 spends · 1 refund (+₹2.00)");
    expect(s.rows).toBe(7);
  });

  it("adds up every card on the bill and words the plain case", () => {
    const s = unbilledSummary([
      { unbilled: { total: 400, count: 1 } },
      { unbilled: { total: 600, count: 2, credits: { total: 0, count: 0 } } },
    ]);
    expect(s).toMatchObject({ total: 1000, count: 3, rows: 3 });
    expect(s.text).toBe("3 transactions");
    expect(unbilledSummary([{ unbilled: { total: 50, count: 1 } }]).text).toBe("1 transaction");
  });

  it("copes with a card that reported no unbilled block", () => {
    expect(unbilledSummary([{}])).toMatchObject({
      total: 0,
      count: 0,
      rows: 0,
      text: "0 transactions",
    });
  });
});
