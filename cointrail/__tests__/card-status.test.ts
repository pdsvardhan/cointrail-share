import { describe, it, expect, beforeAll, afterAll } from "vitest";
import {
  PAYMENT_STATUSES,
  dueDayLabel,
  isCurrentCycle,
  isDueDateEstimated,
  remainingDue,
  resolvePaymentStatus,
  statementAmountDue,
  summarizeCards,
  type StatusCard,
} from "@/lib/card-status";

/**
 * iter-18 brief contract 3: the cards page uses Ingest's payment_status / remaining_due /
 * current_cycle / latest_statement.{due_date, due_date_source, amount_due}, and falls back
 * to the pre-contract fields (is_paid, payment, actual_due, total) for an older Ingest.
 * Amounts below mirror the 2026-10-19 audit's card table.
 */

const originalTZ = process.env.TZ;
beforeAll(() => {
  process.env.TZ = "Asia/Kolkata";
});
afterAll(() => {
  if (originalTZ === undefined) delete process.env.TZ;
  else process.env.TZ = originalTZ;
});

// 2026-10-21 09:00 IST
const NOW = new Date(Date.UTC(2026, 9, 21, 3, 30));

function card(over: Partial<StatusCard> & { stmt?: StatusCard["latest_statement"] }): StatusCard {
  const { stmt, ...rest } = over;
  return {
    latest_statement:
      stmt === undefined
        ? { total: 5110, billing_end: "2026-09-21", due_date: "2026-10-10" }
        : stmt,
    is_paid: false,
    payment: null,
    ...rest,
  };
}

describe("resolvePaymentStatus", () => {
  it("uses Ingest's payment_status verbatim for every contract value", () => {
    for (const s of PAYMENT_STATUSES) {
      // is_paid / due date deliberately disagree: the server's verdict wins.
      expect(resolvePaymentStatus(card({ payment_status: s, is_paid: true }), NOW)).toBe(s);
    }
  });

  it("never turns a server 'not_confirmed' into overdue even when the due date passed", () => {
    const c = card({
      payment_status: "not_confirmed",
      stmt: { total: 179.68, billing_end: "2026-08-20", due_date: "2026-09-09" },
    });
    expect(resolvePaymentStatus(c, NOW)).toBe("not_confirmed");
  });

  describe("fallback for an older Ingest (no payment_status)", () => {
    it("no statement -> no_dues; is_paid -> paid", () => {
      expect(resolvePaymentStatus(card({ stmt: null }), NOW)).toBe("no_dues");
      expect(resolvePaymentStatus(card({ is_paid: true }), NOW)).toBe("paid");
    });

    it("an unknown status string falls back instead of crashing", () => {
      expect(resolvePaymentStatus(card({ payment_status: "mystery", is_paid: true }), NOW)).toBe(
        "paid"
      );
    });

    it("is not overdue on the due date itself (local calendar, M9)", () => {
      const c = card({
        stmt: { total: 13836.54, billing_end: "2026-10-01", due_date: "2026-10-21" },
      });
      expect(resolvePaymentStatus(c, NOW)).toBe("unpaid");
    });

    it("is overdue from the next day, partial when something was paid", () => {
      const s = { total: 13836.54, billing_end: "2026-10-01", due_date: "2026-10-20" };
      expect(resolvePaymentStatus(card({ stmt: s }), NOW)).toBe("overdue");
      const future = { ...s, due_date: "2026-10-25" };
      expect(resolvePaymentStatus(card({ stmt: future, payment: { total: 5000 } }), NOW)).toBe(
        "partial"
      );
    });

    it("a zero or credit-balance bill has no dues", () => {
      expect(resolvePaymentStatus(card({ stmt: { total: 0, due_date: "2026-10-01" } }), NOW)).toBe(
        "no_dues"
      );
    });
  });
});

describe("statementAmountDue / remainingDue", () => {
  it("prefers amount_due (net or bank-stated) over the gross statement total (H2)", () => {
    // Sep bill: 5,110 spent - 511 cashback = 4,599 due.
    const c = card({ stmt: { total: 5110, amount_due: 4599, due_date: "2026-10-10" } });
    expect(statementAmountDue(c)).toBe(4599);
  });

  it("falls back to the older bank-stated actual_due, then to total", () => {
    expect(statementAmountDue(card({ actual_due: 4599 }))).toBe(4599);
    expect(statementAmountDue(card({}))).toBe(5110);
    expect(statementAmountDue(card({ stmt: null }))).toBe(0);
  });

  it("uses the server's remaining_due, clamped at zero", () => {
    expect(remainingDue(card({ remaining_due: 1051.38, payment_status: "unpaid" }), NOW)).toBe(
      1051.38
    );
    expect(remainingDue(card({ remaining_due: -20, payment_status: "paid" }), NOW)).toBe(0);
  });

  it("fallback: amount due minus payments since close; 0 once settled", () => {
    const s = { total: 13836.54, billing_end: "2026-10-01", due_date: "2026-10-25" };
    expect(remainingDue(card({ stmt: s, payment: { total: 3836.54 } }), NOW)).toBe(10000);
    expect(remainingDue(card({ stmt: s, is_paid: true, payment: { total: 13500 } }), NOW)).toBe(0);
  });
});

describe("isCurrentCycle / isDueDateEstimated", () => {
  it("uses the server's current_cycle when present", () => {
    expect(isCurrentCycle(card({ current_cycle: false }), NOW)).toBe(false);
    expect(
      isCurrentCycle(card({ current_cycle: true, stmt: { billing_end: "2026-03-01" } }), NOW)
    ).toBe(true);
  });

  it("fallback: statement closed within the last 45 days", () => {
    const closed = (billing_end: string) => card({ stmt: { total: 1, billing_end } });
    expect(isCurrentCycle(closed("2026-10-21"), NOW)).toBe(true);
    expect(isCurrentCycle(closed("2026-09-06"), NOW)).toBe(true); // 45 days
    expect(isCurrentCycle(closed("2026-09-05"), NOW)).toBe(false); // 46 days
    expect(isCurrentCycle(card({ stmt: { total: 1, billing_end: null } }), NOW)).toBe(false);
  });

  it("flags only an explicitly estimated due date", () => {
    expect(isDueDateEstimated(card({ stmt: { due_date_source: "estimated" } }))).toBe(true);
    expect(isDueDateEstimated(card({ stmt: { due_date_source: "bank" } }))).toBe(false);
    expect(isDueDateEstimated(card({}))).toBe(false);
  });
});

describe("summarizeCards", () => {
  const cards: StatusCard[] = [
    // Current cycle, paid in full after cashback.
    card({
      payment_status: "paid",
      current_cycle: true,
      remaining_due: 0,
      payment: { total: 4599 },
    }),
    // Current cycle, nothing paid yet.
    card({ payment_status: "unpaid", current_cycle: true, remaining_due: 13836.54 }),
    // Current cycle, bank due date, partly paid.
    card({
      payment_status: "partial",
      current_cycle: true,
      remaining_due: 1051.38,
      payment: { total: 500 },
    }),
    // Past due.
    card({ payment_status: "overdue", current_cycle: true, remaining_due: 2000 }),
    // Payment could not be confirmed: neither owed nor settled in the header.
    card({ payment_status: "not_confirmed", current_cycle: true, remaining_due: 179.68 }),
    // Dormant card: its March payment is not "this cycle" (audit: header showed 89,019).
    card({
      payment_status: "paid",
      current_cycle: false,
      remaining_due: 0,
      payment: { total: 21206 },
    }),
    // No statement at all.
    card({ payment_status: "no_dues", stmt: null }),
  ];

  it("Total Due sums remaining_due over unpaid / partial / overdue only", () => {
    const s = summarizeCards(cards, NOW);
    expect(s.totalDue).toBe(16887.92);
    expect(s.owingCount).toBe(3);
    expect(s.notConfirmedCount).toBe(1);
  });

  it("Paid This Cycle counts only current-cycle statements", () => {
    const s = summarizeCards(cards, NOW);
    expect(s.paidThisCycle).toBe(5099);
    expect(s.currentCycleCount).toBe(5);
    expect(s.currentCycleSettledCount).toBe(1);
  });

  it("works on an older Ingest payload without the contract fields", () => {
    const legacy: StatusCard[] = [
      card({
        is_paid: true,
        payment: { total: 46088 },
        stmt: { total: 46088, billing_end: "2026-09-20", due_date: "2026-10-10" },
      }),
      card({ stmt: { total: 13836.54, billing_end: "2026-10-01", due_date: "2026-10-21" } }),
      card({
        is_paid: true,
        payment: { total: 21206 },
        stmt: { total: 21206, billing_end: "2026-03-05", due_date: "2026-03-25" },
      }),
    ];
    const s = summarizeCards(legacy, NOW);
    expect(s.totalDue).toBe(13836.54);
    expect(s.owingCount).toBe(1);
    expect(s.paidThisCycle).toBe(46088);
    expect(s.currentCycleCount).toBe(2);
    expect(s.currentCycleSettledCount).toBe(1);
  });
});

describe("dueDayLabel", () => {
  it("never says '0 days overdue' when Ingest says overdue (M9)", () => {
    expect(dueDayLabel(0, true)).toBe("Overdue");
    expect(dueDayLabel(-1, true)).toBe("1 day overdue");
    expect(dueDayLabel(-7, true)).toBe("7 days overdue");
  });

  it("does not call a past date 'overdue' unless Ingest does", () => {
    expect(dueDayLabel(-12, false)).toBe("12 days ago");
  });

  it("counts down to the due date", () => {
    expect(dueDayLabel(0, false)).toBe("Due today");
    expect(dueDayLabel(1, false)).toBe("1 day remaining");
    expect(dueDayLabel(9, false)).toBe("9 days remaining");
  });
});
