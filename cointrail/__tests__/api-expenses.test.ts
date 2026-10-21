import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

/**
 * Tests for app/api/expenses/route.ts (GET + POST).
 * - DB: real :memory:-style temp file per test, exercising the migrator
 * - Ingest: vi.mock'd so we exercise the route's local + ingest merge logic
 */

function makeTmpDbPath() {
  return path.join(
    os.tmpdir(),
    `cointrail-api-expenses-test-${Date.now()}-${Math.random().toString(36).slice(2)}.db`
  );
}

describe("GET /api/expenses", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = makeTmpDbPath();
    process.env.DB_PATH = tmp;
    process.env.INGEST_URL = "http://test-ingest:9999";
    process.env.INGEST_API_KEY = "test-key";
    vi.resetModules();
  });

  afterEach(() => {
    delete process.env.DB_PATH;
    try {
      fs.unlinkSync(tmp);
    } catch {}
    try {
      fs.unlinkSync(tmp + "-wal");
    } catch {}
    try {
      fs.unlinkSync(tmp + "-shm");
    } catch {}
    vi.unstubAllGlobals();
  });

  it("returns empty merged list when DB is empty and ingest returns no transactions", async () => {
    vi.doMock("@/lib/ingest", () => ({
      ingestFetch: vi.fn().mockResolvedValue({ transactions: [] }),
    }));
    const { GET } = await import("@/app/api/expenses/route");
    const res = await GET(new Request("http://localhost/api/expenses"));
    const data = await res.json();
    expect(Array.isArray(data.expenses)).toBe(true);
    expect(data.expenses).toHaveLength(0);
    expect(data.txnCount).toBe(0);
    expect(Array.isArray(data.categorySummary)).toBe(true);
  });

  it("merges local manual expenses with ingest transactions, newest first", async () => {
    const ingestFetch = vi
      .fn()
      .mockResolvedValueOnce({
        transactions: [
          {
            id: 1,
            amount: 200,
            category: "Transport",
            date: "2026-05-10",
            source: "card",
            merchant: "Uber",
          },
        ],
      })
      .mockResolvedValue({ transactions: [] }); // prevMonth call
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));

    const { getDb } = await import("@/lib/db");
    const { ensureMigrated } = await import("@/lib/migrate");
    ensureMigrated();
    getDb()
      .prepare(
        "INSERT INTO expenses (amount, category, date, description, source) VALUES (?, ?, ?, ?, ?)"
      )
      .run(500, "Food & Dining", "2026-05-15", "Local grocer", "manual");

    const { GET } = await import("@/app/api/expenses/route");
    const res = await GET(new Request("http://localhost/api/expenses"));
    const data = await res.json();
    expect(data.expenses.map((e: any) => e.description)).toEqual(["Local grocer", "Uber"]);
    expect(data.txnCount).toBe(2);
  });

  it("filters CC bill payments out of ingest results", async () => {
    const ingestFetch = vi
      .fn()
      .mockResolvedValueOnce({
        transactions: [
          {
            id: 1,
            amount: 5000,
            source: "sms",
            merchant: "CRED payment",
            category: "Transfer",
            date: "2026-05-10",
          },
          {
            id: 2,
            amount: 250,
            source: "sms",
            merchant: "Swiggy",
            category: "Food & Dining",
            date: "2026-05-11",
          },
        ],
      })
      .mockResolvedValue({ transactions: [] });
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));

    const { GET } = await import("@/app/api/expenses/route");
    const res = await GET(new Request("http://localhost/api/expenses"));
    const data = await res.json();
    const descriptions = data.expenses.map((e: any) => e.description);
    expect(descriptions).toContain("Swiggy");
    expect(descriptions).not.toContain("CRED payment");
  });

  it("respects the source=manual filter and excludes ingest rows", async () => {
    const ingestFetch = vi.fn().mockResolvedValue({
      transactions: [
        {
          id: 1,
          amount: 100,
          source: "card",
          merchant: "Amazon",
          category: "Shopping",
          date: "2026-05-12",
        },
      ],
    });
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));

    const { getDb } = await import("@/lib/db");
    const { ensureMigrated } = await import("@/lib/migrate");
    ensureMigrated();
    getDb()
      .prepare(
        "INSERT INTO expenses (amount, category, date, description, source) VALUES (?, ?, ?, ?, ?)"
      )
      .run(50, "Personal", "2026-05-15", "Manual entry", "manual");

    const { GET } = await import("@/app/api/expenses/route");
    const res = await GET(new Request("http://localhost/api/expenses?source=manual"));
    const data = await res.json();
    expect(data.expenses).toHaveLength(1);
    expect(data.expenses[0].description).toBe("Manual entry");
  });

  it("respects the limit query parameter", async () => {
    vi.doMock("@/lib/ingest", () => ({
      ingestFetch: vi.fn().mockResolvedValue({ transactions: [] }),
    }));

    const { getDb } = await import("@/lib/db");
    const { ensureMigrated } = await import("@/lib/migrate");
    ensureMigrated();
    const stmt = getDb().prepare(
      "INSERT INTO expenses (amount, category, date, description, source) VALUES (?, ?, ?, ?, ?)"
    );
    for (let i = 0; i < 10; i++) {
      stmt.run(
        100 + i,
        "Personal",
        `2026-05-${String(i + 1).padStart(2, "0")}`,
        `Row ${i}`,
        "manual"
      );
    }

    const { GET } = await import("@/app/api/expenses/route");
    const res = await GET(new Request("http://localhost/api/expenses?limit=3"));
    const data = await res.json();
    expect(data.expenses).toHaveLength(3);
  });

  it("survives ingest fetch failure and returns local-only results", async () => {
    vi.doMock("@/lib/ingest", () => ({
      ingestFetch: vi.fn().mockRejectedValue(new Error("ingest down")),
    }));

    const { getDb } = await import("@/lib/db");
    const { ensureMigrated } = await import("@/lib/migrate");
    ensureMigrated();
    getDb()
      .prepare(
        "INSERT INTO expenses (amount, category, date, description, source) VALUES (?, ?, ?, ?, ?)"
      )
      .run(99, "Personal", "2026-05-15", "Manual entry", "manual");

    const { GET } = await import("@/app/api/expenses/route");
    const res = await GET(new Request("http://localhost/api/expenses"));
    const data = await res.json();
    expect(data.expenses).toHaveLength(1);
    expect(data.expenses[0].description).toBe("Manual entry");
  });

  it("returns a categorySummary aggregated by category sorted by total desc", async () => {
    vi.doMock("@/lib/ingest", () => ({
      ingestFetch: vi.fn().mockResolvedValue({ transactions: [] }),
    }));

    const { getDb } = await import("@/lib/db");
    const { ensureMigrated } = await import("@/lib/migrate");
    ensureMigrated();
    const stmt = getDb().prepare(
      "INSERT INTO expenses (amount, category, date, description, source) VALUES (?, ?, ?, ?, ?)"
    );
    stmt.run(100, "Food & Dining", "2026-05-15", "A", "manual");
    stmt.run(200, "Food & Dining", "2026-05-15", "B", "manual");
    stmt.run(50, "Transport", "2026-05-15", "C", "manual");

    const { GET } = await import("@/app/api/expenses/route");
    const res = await GET(new Request("http://localhost/api/expenses"));
    const data = await res.json();
    expect(data.categorySummary[0].category).toBe("Food & Dining");
    expect(data.categorySummary[0].total).toBe(300);
    expect(data.categorySummary[1].category).toBe("Transport");
    expect(data.categorySummary[1].total).toBe(50);
  });
});

describe("POST /api/expenses", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = makeTmpDbPath();
    process.env.DB_PATH = tmp;
    vi.resetModules();
  });

  afterEach(() => {
    delete process.env.DB_PATH;
    try {
      fs.unlinkSync(tmp);
    } catch {}
    try {
      fs.unlinkSync(tmp + "-wal");
    } catch {}
    try {
      fs.unlinkSync(tmp + "-shm");
    } catch {}
  });

  it("inserts a manual expense and returns the new id", async () => {
    const { POST } = await import("@/app/api/expenses/route");
    const req = new Request("http://localhost/api/expenses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        amount: 199.5,
        category: "Food & Dining",
        date: "2026-05-15",
        description: "Test merchant",
      }),
    });
    const res = await POST(req);
    expect(res.status).toBe(201);
    const data = await res.json();
    expect(typeof data.id).toBe("number");
    expect(data.id).toBeGreaterThan(0);

    const { getDb } = await import("@/lib/db");
    const row = getDb().prepare("SELECT * FROM expenses WHERE id = ?").get(data.id) as any;
    expect(row.amount).toBe(199.5);
    expect(row.category).toBe("Food & Dining");
    expect(row.source).toBe("manual"); // default
  });

  it("respects an explicit source field on the body", async () => {
    const { POST } = await import("@/app/api/expenses/route");
    const req = new Request("http://localhost/api/expenses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        amount: 50,
        date: "2026-05-15",
        source: "cash",
        description: "Coffee",
      }),
    });
    const res = await POST(req);
    const data = await res.json();
    const { getDb } = await import("@/lib/db");
    const row = getDb().prepare("SELECT source FROM expenses WHERE id = ?").get(data.id) as any;
    expect(row.source).toBe("cash");
  });
});
