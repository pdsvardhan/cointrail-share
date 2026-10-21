import { describe, it, expect } from "vitest";
import { normalizeDate } from "@/lib/normalize-date";

describe("normalizeDate", () => {
  describe("ISO passthrough", () => {
    it("returns ISO YYYY-MM-DD unchanged", () => {
      expect(normalizeDate("2026-05-16")).toBe("2026-05-16");
      expect(normalizeDate("1999-01-01")).toBe("1999-01-01");
      expect(normalizeDate("2025-12-31")).toBe("2025-12-31");
    });
  });

  describe("DD-MM-YYYY and DD/MM/YYYY", () => {
    it("converts dash-separated", () => {
      expect(normalizeDate("16-05-2026")).toBe("2026-05-16");
      expect(normalizeDate("01-01-2025")).toBe("2025-01-01");
    });

    it("converts slash-separated", () => {
      expect(normalizeDate("16/05/2026")).toBe("2026-05-16");
      expect(normalizeDate("31/12/2025")).toBe("2025-12-31");
    });

    it("pads single-digit day and month", () => {
      expect(normalizeDate("1-5-2026")).toBe("2026-05-01");
      expect(normalizeDate("9/9/2024")).toBe("2024-09-09");
    });
  });

  describe("DD-Mon-YYYY (Indian bank PDF style)", () => {
    it("recognises all 12 lowercase month abbreviations", () => {
      expect(normalizeDate("15-jan-2026")).toBe("2026-01-15");
      expect(normalizeDate("28-feb-2025")).toBe("2025-02-28");
      expect(normalizeDate("10-mar-2026")).toBe("2026-03-10");
      expect(normalizeDate("01-apr-2025")).toBe("2025-04-01");
      expect(normalizeDate("12-may-2026")).toBe("2026-05-12");
      expect(normalizeDate("21-jun-2026")).toBe("2026-06-21");
      expect(normalizeDate("04-jul-2024")).toBe("2024-07-04");
      expect(normalizeDate("18-aug-2025")).toBe("2025-08-18");
      expect(normalizeDate("30-sep-2026")).toBe("2026-09-30");
      expect(normalizeDate("11-oct-2025")).toBe("2025-10-11");
      expect(normalizeDate("23-nov-2026")).toBe("2026-11-23");
      expect(normalizeDate("25-dec-2025")).toBe("2025-12-25");
    });

    it("is case-insensitive for the month token", () => {
      expect(normalizeDate("16-MAY-2026")).toBe("2026-05-16");
      expect(normalizeDate("16-May-2026")).toBe("2026-05-16");
      expect(normalizeDate("16-mAy-2026")).toBe("2026-05-16");
    });

    it("accepts slash separator as well", () => {
      expect(normalizeDate("16/May/2026")).toBe("2026-05-16");
    });

    it("falls back to original string for unrecognised month name", () => {
      expect(normalizeDate("16-foo-2026")).toBe("16-foo-2026");
    });
  });

  describe("DD-MM-YY (2-digit year, Y2K split at 50)", () => {
    it("maps years >50 to 19xx", () => {
      expect(normalizeDate("01-01-99")).toBe("1999-01-01");
      expect(normalizeDate("15/05/51")).toBe("1951-05-15");
    });

    it("maps years <=50 to 20xx", () => {
      expect(normalizeDate("01-01-50")).toBe("2050-01-01");
      expect(normalizeDate("16/05/26")).toBe("2026-05-16");
      expect(normalizeDate("01/01/00")).toBe("2000-01-01");
    });
  });

  describe("empty input", () => {
    it("returns today's ISO date for empty string", () => {
      const today = new Date().toISOString().split("T")[0];
      expect(normalizeDate("")).toBe(today);
    });
  });

  describe("unrecognised input", () => {
    it("returns original string for completely unparseable input", () => {
      expect(normalizeDate("not a date")).toBe("not a date");
      expect(normalizeDate("2026-13-45")).toBe("2026-13-45"); // matches ISO pattern; passthrough
      expect(normalizeDate("16.05.2026")).toBe("16.05.2026"); // dots not supported
    });
  });
});
