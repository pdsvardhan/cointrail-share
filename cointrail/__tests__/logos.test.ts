import { describe, it, expect } from "vitest";
import { getMerchantLogoUrl, getBankLogoUrl } from "@/lib/logos";

describe("getMerchantLogoUrl", () => {
  it("returns Google Favicon URL for known merchants", () => {
    const url = getMerchantLogoUrl("swiggy");
    expect(url).toBe("https://www.google.com/s2/favicons?domain=swiggy.com&sz=32");
  });

  it("handles case insensitivity", () => {
    expect(getMerchantLogoUrl("AMAZON")).toContain("amazon.in");
    expect(getMerchantLogoUrl("Netflix")).toContain("netflix.com");
  });

  it("matches partial merchant names", () => {
    expect(getMerchantLogoUrl("SWIGGY ORDER #123")).toContain("swiggy.com");
    expect(getMerchantLogoUrl("uber trip")).toContain("uber.com");
  });

  it("returns null for unknown merchants", () => {
    expect(getMerchantLogoUrl("totally unknown shop xyz")).toBeNull();
  });

  it("returns null for empty string", () => {
    expect(getMerchantLogoUrl("")).toBeNull();
  });

  it("respects custom size", () => {
    const url = getMerchantLogoUrl("zomato", 64);
    expect(url).toContain("sz=64");
  });
});

describe("getBankLogoUrl", () => {
  it("returns logo for known banks", () => {
    expect(getBankLogoUrl("hdfc")).toContain("hdfcbank.com");
    expect(getBankLogoUrl("icici")).toContain("icicibank.com");
    expect(getBankLogoUrl("sbi")).toContain("sbi.co.in");
  });

  it("returns null for unknown banks", () => {
    expect(getBankLogoUrl("random bank")).toBeNull();
  });

  it("returns null for empty input", () => {
    expect(getBankLogoUrl("")).toBeNull();
  });
});
