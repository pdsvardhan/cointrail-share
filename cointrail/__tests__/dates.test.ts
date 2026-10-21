import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { parseLocalDate, toLocalISODate, daysUntil } from "@/lib/dates";

/**
 * iter-18 M9: `YYYY-MM-DD` strings must be treated as local calendar dates. These tests
 * run in Asia/Kolkata (UTC+05:30), where the old `new Date("YYYY-MM-DD")` parse (UTC
 * midnight = 05:30 IST) flipped a card to "Overdue" at 05:30 IST on its due date.
 */

const originalTZ = process.env.TZ;

beforeAll(() => {
  process.env.TZ = "Asia/Kolkata";
});

afterAll(() => {
  if (originalTZ === undefined) delete process.env.TZ;
  else process.env.TZ = originalTZ;
});

// An IST wall-clock instant, built from UTC so it does not depend on the runner's zone.
function ist(y: number, mo: number, d: number, h: number, mi: number): Date {
  return new Date(Date.UTC(y, mo - 1, d, h, mi) - 330 * 60 * 1000);
}

describe("timezone precondition", () => {
  it("runs these tests in IST", () => {
    expect(new Date(2026, 9, 21).getTimezoneOffset()).toBe(-330);
  });
});

describe("parseLocalDate", () => {
  it("returns local midnight of the calendar date", () => {
    const d = parseLocalDate("2026-10-21")!;
    expect([d.getFullYear(), d.getMonth(), d.getDate(), d.getHours()]).toEqual([2026, 9, 21, 0]);
  });

  it("accepts a timestamp and keeps only its date part", () => {
    expect(toLocalISODate(parseLocalDate("2026-10-21T18:45:00")!)).toBe("2026-10-21");
  });

  it("returns null for missing, malformed or impossible dates", () => {
    expect(parseLocalDate(null)).toBeNull();
    expect(parseLocalDate(undefined)).toBeNull();
    expect(parseLocalDate("")).toBeNull();
    expect(parseLocalDate("21-10-2026")).toBeNull();
    expect(parseLocalDate("2026-02-30")).toBeNull();
  });
});

describe("daysUntil", () => {
  it("is 0 on the due date itself after 05:30 IST (old UTC parse said overdue)", () => {
    const now = ist(2026, 10, 21, 6, 0);
    // The bug being fixed: the UTC parse puts the due date at 05:30 IST, already "past".
    expect(new Date("2026-10-21") < now).toBe(true);
    expect(daysUntil("2026-10-21", now)).toBe(0);
  });

  it("is 0 until the last minute of the due date in IST", () => {
    expect(daysUntil("2026-10-21", ist(2026, 10, 21, 0, 1))).toBe(0);
    expect(daysUntil("2026-10-21", ist(2026, 10, 21, 23, 59))).toBe(0);
  });

  it("goes negative from 00:00 IST the next day", () => {
    expect(daysUntil("2026-10-21", ist(2026, 10, 22, 0, 0))).toBe(-1);
    expect(daysUntil("2026-10-21", ist(2026, 10, 28, 12, 0))).toBe(-7);
  });

  it("counts whole days ahead regardless of the time of day", () => {
    expect(daysUntil("2026-11-01", ist(2026, 10, 21, 23, 59))).toBe(11);
    expect(daysUntil("2027-01-01", ist(2026, 12, 31, 0, 30))).toBe(1);
  });

  it("is null for an unusable date", () => {
    expect(daysUntil(null)).toBeNull();
    expect(daysUntil("soon")).toBeNull();
  });
});

describe("toLocalISODate", () => {
  it("formats the local calendar date (toISOString would give the previous day in IST)", () => {
    const firstOfMonth = new Date(2026, 9, 1); // local midnight, 2026-09-30T18:30Z
    expect(firstOfMonth.toISOString().slice(0, 10)).toBe("2026-09-30");
    expect(toLocalISODate(firstOfMonth)).toBe("2026-10-01");
  });

  it("round-trips with parseLocalDate", () => {
    for (const iso of ["2026-01-01", "2026-02-28", "2028-02-29", "2026-12-31"]) {
      expect(toLocalISODate(parseLocalDate(iso)!)).toBe(iso);
    }
  });
});
