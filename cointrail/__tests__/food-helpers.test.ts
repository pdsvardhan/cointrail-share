import { describe, it, expect } from "vitest";
import {
  extractRestaurant,
  extractAmount,
  getOrderType,
  getPlatform,
  extractDate,
  isOrderEmail,
} from "@/lib/food-helpers";

describe("extractRestaurant", () => {
  it("extracts from 'order from X has been' pattern", () => {
    expect(extractRestaurant("Your order from Paradise Biryani has been delivered", "")).toBe(
      "Paradise Biryani"
    );
  });

  it("extracts from 'ordering from X ORDER' pattern", () => {
    expect(extractRestaurant("Thank you for ordering from Dominos ORDER ID: 123", "")).toBe(
      "Dominos"
    );
  });

  it("returns null when no restaurant found", () => {
    expect(extractRestaurant("Your payment was successful", "")).toBeNull();
  });
});

describe("extractAmount", () => {
  it("extracts Rs. amount", () => {
    expect(extractAmount("Total: Rs. 499.00")).toBe(499.0);
    expect(extractAmount("Rs.1,299.50 paid")).toBe(1299.5);
  });

  it("extracts ₹ amount", () => {
    expect(extractAmount("₹250.00")).toBe(250.0);
  });

  it("extracts from amount label", () => {
    expect(extractAmount("total: 899.00")).toBe(899.0);
  });

  it("returns null when no amount found", () => {
    expect(extractAmount("Your order has been placed")).toBeNull();
  });
});

describe("getOrderType", () => {
  it("detects dineout", () => {
    expect(getOrderType("Dineout booking confirmed", "", "noreply@swiggy.in")).toBe("dineout");
  });

  it("detects instamart", () => {
    expect(getOrderType("Instamart order delivered", "", "noreply@swiggy.in")).toBe("instamart");
  });

  it("detects blinkit from sender", () => {
    expect(getOrderType("Order delivered", "", "noreply@blinkit.com")).toBe("blinkit");
  });

  it("detects gourmet", () => {
    expect(getOrderType("Swiggy Gourmet order confirmed", "", "noreply@swiggy.in")).toBe("gourmet");
  });

  it("defaults to delivery", () => {
    expect(getOrderType("Your food is on its way", "", "noreply@swiggy.in")).toBe("delivery");
  });
});

describe("getPlatform", () => {
  it("identifies swiggy", () => {
    expect(getPlatform("noreply@swiggy.in")).toBe("swiggy");
  });

  it("identifies zomato", () => {
    expect(getPlatform("orders@zomato.com")).toBe("zomato");
  });

  it("identifies blinkit", () => {
    expect(getPlatform("no-reply@blinkit.com")).toBe("blinkit");
  });

  it("returns other for unknown", () => {
    expect(getPlatform("hello@example.com")).toBe("other");
  });
});

describe("extractDate", () => {
  it("extracts ISO date", () => {
    expect(extractDate("2025-06-15")).toBe("2025-06-15");
  });

  it("parses RFC 2822 date", () => {
    const result = extractDate("Wed, 8 Oct 2025 16:09:34 +0530");
    expect(result).toBe("2025-10-08");
  });

  it("returns null for empty string", () => {
    expect(extractDate("")).toBeNull();
  });
});

describe("isOrderEmail", () => {
  it("matches order subjects", () => {
    expect(isOrderEmail("Your order from Paradise has been delivered")).toBe(true);
    expect(isOrderEmail("Order #1234 confirmed")).toBe(true);
  });

  it("matches delivery subjects", () => {
    expect(isOrderEmail("Your delivery is on the way")).toBe(true);
  });

  it("matches dineout subjects", () => {
    expect(isOrderEmail("Dineout booking confirmed")).toBe(true);
  });

  it("rejects non-order subjects", () => {
    expect(isOrderEmail("Your weekly newsletter")).toBe(false);
    expect(isOrderEmail("Account security alert")).toBe(false);
  });
});
