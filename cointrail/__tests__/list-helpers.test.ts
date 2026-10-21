import { describe, it, expect, vi, afterEach } from "vitest";
import { summarizeFood } from "@/lib/food-summary";
import { signedAmountParts, signedOf } from "@/lib/txn-display";
import { clampIndex } from "@/lib/ui";
import { HttpError, getJson } from "@/lib/http";

/** Iteration 203 E2: M10 (food headline), M3/M4 (signed rows), B5 (carousel), M11 (errors). */

describe("Food headline ignores search and sort (M10)", () => {
  const orders = [
    { restaurant_name: "Toit", total_amount: 900, order_date: "2026-10-01" },
    { restaurant_name: "Toit", total_amount: 900, order_date: "2026-10-02" },
    { restaurant_name: "Pizza Hut", total_amount: 1500, order_date: "2026-10-03" },
    { restaurant_name: "Chai Point", total_amount: 100, order_date: "2026-10-04" },
  ];

  it("the cards come from every order, spend-sorted", () => {
    for (const [query, sort] of [
      ["", "total"],
      ["pizza", "total"],
      ["", "avg"],
      ["chai", "count"],
    ] as const) {
      const s = summarizeFood(orders, query, sort);
      expect(s.total).toBe(3400);
      expect(s.orderCount).toBe(4);
      expect(s.top?.name).toBe("Toit"); // 1800 beats Pizza Hut's 1500 even when sorted by Avg
      expect(s.restaurantCount).toBe(3);
    }
  });

  it("search and sort shape only the grid", () => {
    expect(summarizeFood(orders, "pizza", "total").grid.map((r) => r.name)).toEqual(["Pizza Hut"]);
    expect(summarizeFood(orders, "", "avg").grid.map((r) => r.name)).toEqual([
      "Pizza Hut",
      "Toit",
      "Chai Point",
    ]);
  });
});

describe("signed amounts in lists (M3 / M4)", () => {
  it("refunds are written with a plus and flagged as credits", () => {
    expect(signedAmountParts(-300)).toEqual({ text: "+₹300.00", isCredit: true });
    expect(signedAmountParts(1200)).toEqual({ text: "₹1,200.00", isCredit: false });
    expect(signedAmountParts(1200, true).text).toBe("-₹1,200.00");
  });

  it("reads signed_amount, and falls back to txn_type on an older Ingest", () => {
    expect(signedOf({ amount: 300, signed_amount: -300 })).toBe(-300);
    expect(signedOf({ amount: 300, txn_type: "credit" })).toBe(-300);
    expect(signedOf({ amount: 300, txn_type: "debit" })).toBe(300);
  });
});

describe("mobile carousel index (B5)", () => {
  it("stays inside the filtered list", () => {
    expect(clampIndex(4, 2)).toBe(1);
    expect(clampIndex(-1, 3)).toBe(0);
    expect(clampIndex(1, 3)).toBe(1);
    expect(clampIndex(3, 0)).toBe(0);
  });
});

describe("getJson fails loudly (M11)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("throws HttpError on a 502 instead of returning the empty body", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ error: "Ingest unavailable", cards: [] }), { status: 502 })
        )
    );
    await expect(getJson("/api/cards")).rejects.toBeInstanceOf(HttpError);
    await expect(getJson("/api/cards")).rejects.toMatchObject({ status: 502 });
  });

  it("returns the body on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ cards: [1] }), { status: 200 }))
    );
    expect(await getJson("/api/cards")).toEqual({ cards: [1] });
  });
});
