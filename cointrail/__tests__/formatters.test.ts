import { describe, it, expect } from "vitest";
import { formatIndianNumber, formatCompact, autoFormatCurrency } from "@/lib/formatters";

describe("formatIndianNumber", () => {
  it("formats small numbers", () => {
    expect(formatIndianNumber(100)).toBe("₹100.00");
    expect(formatIndianNumber(999)).toBe("₹999.00");
  });

  it("formats thousands with comma", () => {
    expect(formatIndianNumber(1000)).toBe("₹1,000.00");
    expect(formatIndianNumber(10000)).toBe("₹10,000.00");
    expect(formatIndianNumber(99999)).toBe("₹99,999.00");
  });

  it("formats lakhs with Indian grouping", () => {
    expect(formatIndianNumber(100000)).toBe("₹1,00,000.00");
    expect(formatIndianNumber(1234567)).toBe("₹12,34,567.00");
  });

  it("formats crores", () => {
    expect(formatIndianNumber(10000000)).toBe("₹1,00,00,000.00");
    expect(formatIndianNumber(12345678.9)).toBe("₹1,23,45,678.90");
  });

  it("handles decimals", () => {
    expect(formatIndianNumber(1499.5)).toBe("₹1,499.50");
    expect(formatIndianNumber(0.99)).toBe("₹0.99");
  });

  it("handles negative numbers", () => {
    expect(formatIndianNumber(-5000)).toBe("-₹5,000.00");
    expect(formatIndianNumber(-123456)).toBe("-₹1,23,456.00");
  });

  it("handles zero", () => {
    expect(formatIndianNumber(0)).toBe("₹0.00");
  });
});

describe("formatCompact", () => {
  it("returns full format for small amounts", () => {
    expect(formatCompact(5000)).toBe("₹5,000.00");
    expect(formatCompact(99999)).toBe("₹99,999.00");
  });

  it("formats lakhs", () => {
    expect(formatCompact(100000)).toBe("₹1.00 L");
    expect(formatCompact(250000)).toBe("₹2.50 L");
    expect(formatCompact(1500000)).toBe("₹15.00 L");
  });

  it("formats crores", () => {
    expect(formatCompact(10000000)).toBe("₹1.00 Cr");
    expect(formatCompact(75000000)).toBe("₹7.50 Cr");
  });

  it("handles negative compact values", () => {
    expect(formatCompact(-500000)).toBe("-₹5.00 L");
    expect(formatCompact(-20000000)).toBe("-₹2.00 Cr");
  });
});

describe("autoFormatCurrency", () => {
  it("delegates to formatIndianNumber", () => {
    expect(autoFormatCurrency(12345)).toBe(formatIndianNumber(12345));
  });
});
