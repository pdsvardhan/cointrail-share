import { describe, it, expect } from "vitest";
import {
  cancelPayload,
  chargedAfterCancelText,
  isValidDay,
  lastChargeAmount,
  totalMonthly,
} from "@/lib/recurring";

/**
 * Iteration 203 E5: "Mark as cancelled" on a recurring series. Ingest takes the series out of the
 * active list (so every total built from that list drops it) and reports a charge after the date.
 */

describe("cancelPayload", () => {
  it("sends the series key, the date and a display name", () => {
    expect(
      cancelPayload(
        { series_key: "sub:linkedin", merchant: "LinkedIn", display_name: "LinkedIn" },
        "2026-10-20"
      )
    ).toEqual({ series_key: "sub:linkedin", cancelled_on: "2026-10-20", display_name: "LinkedIn" });
  });

  it("falls back to the merchant when there is no display name", () => {
    expect(
      cancelPayload({ series_key: "sub:netflix", merchant: "Netflix" }, "2026-10-01").display_name
    ).toBe("Netflix");
  });
});

describe("isValidDay", () => {
  it("accepts a real calendar date only", () => {
    expect(isValidDay("2026-10-20")).toBe(true);
    expect(isValidDay("2026-02-30")).toBe(false);
    expect(isValidDay("20-10-2026")).toBe(false);
    expect(isValidDay("")).toBe(false);
  });
});

describe("chargedAfterCancelText (E5 AC2 on the page)", () => {
  it("is null when nothing charged after the cancellation", () => {
    expect(chargedAfterCancelText({ charged_after_cancel: [] })).toBeNull();
    expect(chargedAfterCancelText({})).toBeNull();
  });

  it("names one charge with its date", () => {
    expect(
      chargedAfterCancelText({
        charged_after_cancel: [{ id: 1, date: "2026-11-02", amount: 999, card_last4: "0077" }],
      })
    ).toBe("Charged ₹999.00 on 2 Nov 2026 after you cancelled it");
  });

  it("sums several charges", () => {
    expect(
      chargedAfterCancelText({
        charged_after_cancel: [
          { id: 1, date: "2026-11-02", amount: 999, card_last4: "0077" },
          { id: 2, date: "2026-12-02", amount: 999, card_last4: "0077" },
        ],
      })
    ).toBe("Charged 2 times after you cancelled it (₹1,998.00)");
  });
});

describe("cancelled series leave the monthly total (E5 AC3)", () => {
  it("Ingest's active list has no cancelled row, so the shared total excludes it", () => {
    const active = [{ type: "subscription", frequency: "monthly", avg_amount: 199 }];
    // LinkedIn is reported under `cancelled`, never in `recurring`.
    expect(totalMonthly(active)).toBe(199);
  });
});

describe("lastChargeAmount (E5 round 2: a cancelled card shows what the series last cost)", () => {
  it("prefers the latest charge over an average of older, cheaper ones", () => {
    expect(lastChargeAmount({ last_amount: 900, avg_amount: 720 })).toBe(900);
  });

  it("falls back to the average from an older Ingest, and to null when neither is known", () => {
    expect(lastChargeAmount({ avg_amount: 720 })).toBe(720);
    expect(lastChargeAmount({ last_amount: null, avg_amount: 720 })).toBe(720);
    expect(lastChargeAmount({})).toBeNull();
  });
});
