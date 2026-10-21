import { describe, it, expect, beforeEach, vi } from "vitest";

/**
 * Tests for the 6 dashboard analytics routes (all simple Ingest proxies).
 */

describe("GET /api/expenses/dashboard", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  // Since 74bf9fb (iter-13 #141) the route asks Ingest /health for the last transaction
  // date when no period is given, so the dashboard opens on the latest month with data.
  // These mocks answer by path instead of by call order (iter-18 CH1).
  function byPath(health: unknown) {
    return vi.fn().mockImplementation(async (path: string) => {
      if (path === "/health") {
        if (health instanceof Error) throw health;
        return health;
      }
      if (path.startsWith("/analytics/overview")) return { total: 5000, count: 25 };
      if (path === "/recurring/detect") return { recurring: [{ name: "Netflix" }] };
      throw new Error(`unexpected path ${path}`);
    });
  }

  it("composes overview + recurring/detect and returns both", async () => {
    const ingestFetch = byPath({ last_transaction: "2026-05-14" });
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/expenses/dashboard/route");
    const res = await GET(new Request("http://localhost/api/expenses/dashboard"));
    const data = await res.json();
    expect(data.overview.total).toBe(5000);
    expect(data.recurring.recurring[0].name).toBe("Netflix");
    expect(ingestFetch).toHaveBeenCalledTimes(3); // /health + overview + recurring
  });

  it("defaults the period to the month of Ingest's last transaction", async () => {
    const ingestFetch = byPath({ last_transaction: "2026-05-14" });
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/expenses/dashboard/route");
    const res = await GET(new Request("http://localhost/api/expenses/dashboard"));
    expect(ingestFetch).toHaveBeenCalledWith("/analytics/overview?period=2026-05");
    expect((await res.json()).period).toBe("2026-05");
  });

  it("falls back to the current YYYY-MM when /health fails or has no last transaction", async () => {
    for (const health of [new Error("health down"), { last_transaction: null }]) {
      vi.resetModules();
      const ingestFetch = byPath(health);
      vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
      const { GET } = await import("@/app/api/expenses/dashboard/route");
      const res = await GET(new Request("http://localhost/api/expenses/dashboard"));
      const { period } = await res.json();
      expect(period).toMatch(/^\d{4}-\d{2}$/);
      expect(ingestFetch).toHaveBeenCalledWith(`/analytics/overview?period=${period}`);
    }
  });

  it("uses the period query when provided and skips /health", async () => {
    const ingestFetch = byPath({ last_transaction: "2026-05-14" });
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/expenses/dashboard/route");
    await GET(new Request("http://localhost/api/expenses/dashboard?period=2026-03"));
    expect(ingestFetch).toHaveBeenCalledWith("/analytics/overview?period=2026-03");
    expect(ingestFetch).not.toHaveBeenCalledWith("/health");
  });

  it("returns 500 + generic error on ingest failure", async () => {
    vi.doMock("@/lib/ingest", () => ({
      ingestFetch: vi.fn().mockRejectedValue(new Error("down")),
    }));
    const { GET } = await import("@/app/api/expenses/dashboard/route");
    const res = await GET(new Request("http://localhost/api/expenses/dashboard"));
    expect(res.status).toBe(500);
    const data = await res.json();
    expect(data.error).toBe("Failed to fetch dashboard overview");
  });
});

describe("GET /api/expenses/dashboard/categories", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("calls ingest with period (default = current YYYY-MM)", async () => {
    const ingestFetch = vi.fn().mockResolvedValue({ categories: [] });
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/expenses/dashboard/categories/route");
    await GET(new Request("http://localhost/api/expenses/dashboard/categories"));
    expect(ingestFetch).toHaveBeenCalledTimes(1);
    expect(ingestFetch.mock.calls[0][0]).toMatch(/^\/analytics\/categories\?period=\d{4}-\d{2}$/);
  });

  it("appends optional category filter (URL-encoded)", async () => {
    const ingestFetch = vi.fn().mockResolvedValue({});
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/expenses/dashboard/categories/route");
    await GET(
      new Request(
        "http://localhost/api/expenses/dashboard/categories?period=2026-04&category=Food%20%26%20Dining"
      )
    );
    expect(ingestFetch).toHaveBeenCalledWith(
      "/analytics/categories?period=2026-04&category=Food%20%26%20Dining"
    );
  });

  it("returns 500 on failure", async () => {
    vi.doMock("@/lib/ingest", () => ({
      ingestFetch: vi.fn().mockRejectedValue(new Error("down")),
    }));
    const { GET } = await import("@/app/api/expenses/dashboard/categories/route");
    const res = await GET(new Request("http://localhost/api/expenses/dashboard/categories"));
    expect(res.status).toBe(500);
  });
});

describe("GET /api/expenses/dashboard/trends", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("defaults to months=12 if not given", async () => {
    const ingestFetch = vi.fn().mockResolvedValue({});
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/expenses/dashboard/trends/route");
    await GET(new Request("http://localhost/api/expenses/dashboard/trends"));
    expect(ingestFetch).toHaveBeenCalledWith("/analytics/trends?months=12");
  });

  it("respects months query param", async () => {
    const ingestFetch = vi.fn().mockResolvedValue({});
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/expenses/dashboard/trends/route");
    await GET(new Request("http://localhost/api/expenses/dashboard/trends?months=3"));
    expect(ingestFetch).toHaveBeenCalledWith("/analytics/trends?months=3");
  });

  it("500 on failure", async () => {
    vi.doMock("@/lib/ingest", () => ({
      ingestFetch: vi.fn().mockRejectedValue(new Error("boom")),
    }));
    const { GET } = await import("@/app/api/expenses/dashboard/trends/route");
    const res = await GET(new Request("http://localhost/api/expenses/dashboard/trends"));
    expect(res.status).toBe(500);
  });
});

describe("GET /api/expenses/dashboard/heatmap", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("defaults to months=6", async () => {
    const ingestFetch = vi.fn().mockResolvedValue({});
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/expenses/dashboard/heatmap/route");
    await GET(new Request("http://localhost/api/expenses/dashboard/heatmap"));
    expect(ingestFetch).toHaveBeenCalledWith("/analytics/heatmap?months=6");
  });
});

describe("GET /api/expenses/dashboard/day-transactions", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("returns 400 when date param missing", async () => {
    vi.doMock("@/lib/ingest", () => ({ ingestFetch: vi.fn() }));
    const { GET } = await import("@/app/api/expenses/dashboard/day-transactions/route");
    const res = await GET(new Request("http://localhost/api/expenses/dashboard/day-transactions"));
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toBe("date parameter required");
  });

  it("calls ingest with date", async () => {
    const ingestFetch = vi.fn().mockResolvedValue({ transactions: [{ id: 1 }] });
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/expenses/dashboard/day-transactions/route");
    const res = await GET(
      new Request("http://localhost/api/expenses/dashboard/day-transactions?date=2026-05-12")
    );
    const data = await res.json();
    expect(ingestFetch).toHaveBeenCalledWith("/analytics/day-transactions?date=2026-05-12");
    expect(data.transactions).toHaveLength(1);
  });
});

describe("GET /api/expenses/dashboard/merchant-detail", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("returns 400 when merchant param missing", async () => {
    vi.doMock("@/lib/ingest", () => ({ ingestFetch: vi.fn() }));
    const { GET } = await import("@/app/api/expenses/dashboard/merchant-detail/route");
    const res = await GET(new Request("http://localhost/api/expenses/dashboard/merchant-detail"));
    expect(res.status).toBe(400);
  });

  it("URL-encodes the merchant and defaults months=12", async () => {
    const ingestFetch = vi.fn().mockResolvedValue({});
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/expenses/dashboard/merchant-detail/route");
    await GET(
      new Request(
        "http://localhost/api/expenses/dashboard/merchant-detail?merchant=Cafe%20Coffee%20Day"
      )
    );
    expect(ingestFetch).toHaveBeenCalledWith(
      "/analytics/merchant-detail?merchant=Cafe%20Coffee%20Day&months=12"
    );
  });

  it("respects months override", async () => {
    const ingestFetch = vi.fn().mockResolvedValue({});
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/expenses/dashboard/merchant-detail/route");
    await GET(
      new Request("http://localhost/api/expenses/dashboard/merchant-detail?merchant=Foo&months=24")
    );
    expect(ingestFetch.mock.calls[0][0]).toContain("months=24");
  });
});

/**
 * Iteration 203 E1: the 3M / 6M / 1Y / All pills send a date range; every dashboard route
 * forwards it to Ingest (they used to read `period` only, so the pills changed nothing).
 */
describe("date ranges reach Ingest (iteration 203 E1)", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  const RANGE = "from=2026-08-01&to=2026-10-20";

  function anyPath() {
    return vi.fn().mockImplementation(async (path: string) => {
      if (path === "/recurring/detect") return { recurring: [] };
      return { path };
    });
  }

  it("dashboard: overview over the range, no /health lookup, range echoed", async () => {
    const ingestFetch = anyPath();
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/expenses/dashboard/route");
    const res = await GET(new Request(`http://localhost/api/expenses/dashboard?${RANGE}`));
    expect(ingestFetch).toHaveBeenCalledWith(`/analytics/overview?${RANGE}`);
    expect(ingestFetch).not.toHaveBeenCalledWith("/health");
    const data = await res.json();
    expect(data.range).toEqual({ from: "2026-08-01", to: "2026-10-20" });
    expect(data.period).toBeNull();
  });

  it("half a range is a 400 everywhere", async () => {
    vi.doMock("@/lib/ingest", () => ({ ingestFetch: anyPath() }));
    const routes = [
      await import("@/app/api/expenses/dashboard/route"),
      await import("@/app/api/expenses/dashboard/categories/route"),
      await import("@/app/api/expenses/dashboard/trends/route"),
      await import("@/app/api/expenses/dashboard/heatmap/route"),
      await import("@/app/api/expenses/dashboard/transactions/route"),
    ];
    for (const { GET } of routes) {
      const res = await GET(new Request("http://localhost/x?from=2026-08-01"));
      expect(res.status).toBe(400);
    }
  });

  it("categories, the category drill, trends and heatmap", async () => {
    const ingestFetch = anyPath();
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const cat = await import("@/app/api/expenses/dashboard/categories/route");
    await cat.GET(new Request(`http://localhost/x?${RANGE}`));
    await cat.GET(new Request(`http://localhost/x?${RANGE}&category=Food%20%26%20Dining`));
    const trends = await import("@/app/api/expenses/dashboard/trends/route");
    await trends.GET(new Request(`http://localhost/x?${RANGE}`));
    const heat = await import("@/app/api/expenses/dashboard/heatmap/route");
    await heat.GET(new Request(`http://localhost/x?${RANGE}`));
    expect(ingestFetch.mock.calls.map((c) => c[0])).toEqual([
      `/analytics/categories?${RANGE}`,
      `/analytics/categories?${RANGE}&category=Food%20%26%20Dining`,
      `/analytics/trends?${RANGE}`,
      `/analytics/heatmap?${RANGE}`,
    ]);
  });

  it("merchant drill: exact merchant, category and window (E2 B4)", async () => {
    const ingestFetch = anyPath();
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/expenses/dashboard/merchant-detail/route");
    await GET(
      new Request(`http://localhost/x?merchant=Swiggy&category=Food%20%26%20Dining&${RANGE}`)
    );
    expect(ingestFetch).toHaveBeenCalledWith(
      `/analytics/merchant-detail?merchant=Swiggy&category=Food%20%26%20Dining&${RANGE}`
    );
  });

  it("View All: the period's full, paged list (E2 M1)", async () => {
    const ingestFetch = anyPath();
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/expenses/dashboard/transactions/route");
    await GET(new Request(`http://localhost/x?${RANGE}&sort=amount&offset=50&limit=50&junk=1`));
    await GET(new Request("http://localhost/x?period=2026-09&sort=date"));
    expect(ingestFetch.mock.calls.map((c) => c[0])).toEqual([
      `/analytics/transactions?${RANGE}&sort=amount&offset=50&limit=50`,
      "/analytics/transactions?period=2026-09&sort=date",
    ]);
  });
});
