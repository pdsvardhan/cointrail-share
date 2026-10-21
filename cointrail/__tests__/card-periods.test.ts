import { describe, it, expect, beforeAll, afterAll } from "vitest";
import {
  cardPeriodLabel,
  cardPeriodOptions,
  cardTransactionsQuery,
  defaultCardPeriod,
  monthsBefore,
} from "@/lib/card-periods";
import { toLocalISODate } from "@/lib/dates";

/** Iteration 203 E2 M7 / C4: the card detail page's period dropdown. Runs in IST. */

const originalTZ = process.env.TZ;
beforeAll(() => {
  process.env.TZ = "Asia/Kolkata";
});
afterAll(() => {
  if (originalTZ === undefined) delete process.env.TZ;
  else process.env.TZ = originalTZ;
});

const TODAY = new Date(2026, 9, 20, 9, 0);
const STMT = { billing_start: "2026-09-02", billing_end: "2026-10-01" };

describe("card detail periods", () => {
  it("'Current Statement' asks Ingest for its unbilled set (M7)", () => {
    expect(cardTransactionsQuery("current_stmt", STMT, TODAY)).toBe("period=unbilled");
    expect(cardPeriodLabel("current_stmt", "2026-10-01")).toBe("Unbilled (since 1 Oct)");
    expect(cardTransactionsQuery("last_stmt", STMT, TODAY)).toBe("from=2026-09-02&to=2026-10-01");
  });

  it("statement options exist only when a statement was captured (M7)", () => {
    expect(cardPeriodOptions(STMT).map((p) => p.key)).toEqual([
      "current_stmt",
      "last_stmt",
      "3m",
      "6m",
      "all",
    ]);
    expect(cardPeriodOptions(null).map((p) => p.key)).toEqual(["3m", "6m", "all"]);
    expect(defaultCardPeriod(null)).toBe("3m");
    expect(defaultCardPeriod(STMT)).toBe("current_stmt");
  });

  it("'Last 3 / 6 Months' are today minus 3 / 6 months (C4)", () => {
    expect(cardTransactionsQuery("3m", STMT, TODAY)).toBe("from=2026-07-20&to=2026-10-20");
    expect(cardTransactionsQuery("6m", STMT, TODAY)).toBe("from=2026-04-20&to=2026-10-20");
    expect(toLocalISODate(monthsBefore(new Date(2026, 4, 31), 3))).toBe("2026-02-28");
    expect(cardTransactionsQuery("all", STMT, TODAY)).toBe("");
  });
});
