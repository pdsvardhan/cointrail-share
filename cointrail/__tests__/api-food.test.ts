import { describe, it, expect, beforeEach, vi } from "vitest";

/**
 * Tests for /api/food/orders. (/api/food had no caller and was removed in iteration 203 E2 C7.)
 *
 * Since 427d3de (iter-16 N1) the route reads PARSED INVOICES from Ingest /food/orders
 * (one call, structured orders with restaurant / items / real totals) instead of
 * regex-scraping Gmail subjects per sender via /email/list. The tests were rewritten to
 * that contract in iter-18 CH1; they previously mocked the email path and went stale.
 */

type Order = {
  order_id: string;
  platform: string;
  restaurant: string;
  order_date: string;
  total: number | null;
  item_count: number | null;
  order_type: string;
  order_type_source: "legacy" | "default";
  items?: { name: string; qty: number; amount: number; is_fee?: boolean }[];
};

function order(over: Partial<Order>): Order {
  return {
    order_id: "o-1",
    platform: "swiggy",
    restaurant: "Toit",
    order_date: "2026-05-15",
    total: 850,
    item_count: 2,
    order_type: "delivery",
    order_type_source: "default",
    items: [
      { name: "Margherita Pizza", qty: 2, amount: 650 },
      { name: "Coke", qty: 1, amount: 200 },
      { name: "Packaging charges", qty: 1, amount: 20, is_fee: true },
    ],
    ...over,
  };
}

function mockOrders(orders: Order[]) {
  return vi.fn().mockImplementation(async (path: string) => {
    if (path.startsWith("/food/orders?")) return { orders };
    throw new Error(`unexpected path ${path}`);
  });
}

describe("GET /api/food/orders (list)", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("maps invoices to the list shape and drops fee lines from items", async () => {
    const ingestFetch = mockOrders([order({})]);
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));

    const { GET } = await import("@/app/api/food/orders/route");
    const res = await GET(new Request("http://localhost/api/food/orders"));
    const data = await res.json();
    expect(data.count).toBe(1);
    expect(data.orders[0]).toEqual({
      order_id: "o-1",
      platform: "swiggy",
      order_type: "delivery",
      restaurant_name: "Toit",
      total_amount: 850,
      order_date: "2026-05-15",
      item_count: 2,
      items: [
        { name: "Margherita Pizza", qty: 2, price: 650 },
        { name: "Coke", qty: 1, price: 200 },
      ],
    });
  });

  it("forwards limit (default 1000) to Ingest", async () => {
    const ingestFetch = mockOrders([]);
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/food/orders/route");
    await GET(new Request("http://localhost/api/food/orders?limit=3"));
    await GET(new Request("http://localhost/api/food/orders"));
    expect(ingestFetch.mock.calls[0][0]).toContain("limit=3");
    expect(ingestFetch.mock.calls[1][0]).toContain("limit=1000");
  });

  it("forwards restaurant / platform filters to Ingest", async () => {
    const ingestFetch = mockOrders([]);
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/food/orders/route");
    await GET(new Request("http://localhost/api/food/orders?restaurant=toit&platform=swiggy"));
    const url: string = ingestFetch.mock.calls[0][0];
    expect(url).toContain("restaurant=toit");
    expect(url).toContain("platform=swiggy");
  });

  it("returns empty list when Ingest has no orders", async () => {
    const ingestFetch = mockOrders([]);
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/food/orders/route");
    const res = await GET(new Request("http://localhost/api/food/orders"));
    const data = await res.json();
    expect(data.orders).toEqual([]);
    expect(data.count).toBe(0);
  });

  it("answers 502 with an empty list plus the error when Ingest fails (E2 M11)", async () => {
    const ingestFetch = vi.fn().mockRejectedValue(new Error("ingest down"));
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/food/orders/route");
    const res = await GET(new Request("http://localhost/api/food/orders"));
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ error: "ingest down", orders: [], count: 0 });
  });
});
