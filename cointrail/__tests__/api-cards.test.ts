import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

/**
 * Tests for the cards API routes (Ingest proxies). /api/cards/statements and
 * /api/cards/sync-gmail had no caller and were removed in iteration 203 E2 C7.
 */

describe("GET /api/cards", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("returns ingest payload when ingest succeeds", async () => {
    vi.doMock("@/lib/ingest", () => ({
      ingestFetch: vi.fn().mockResolvedValue({ cards: [{ id: 1, bank: "HDFC" }] }),
    }));
    const { GET } = await import("@/app/api/cards/route");
    const res = await GET();
    const data = await res.json();
    expect(data.cards).toHaveLength(1);
    expect(data.cards[0].bank).toBe("HDFC");
  });

  it("answers 502 with empty cards + the error when ingest throws (E2 M11)", async () => {
    vi.doMock("@/lib/ingest", () => ({
      ingestFetch: vi.fn().mockRejectedValue(new Error("ingest down")),
    }));
    const { GET } = await import("@/app/api/cards/route");
    const res = await GET();
    expect(res.status).toBe(502);
    const data = await res.json();
    expect(data.cards).toEqual([]);
    expect(data.error).toBe("ingest down");
  });
});

describe("GET /api/cards/[id]/transactions", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  async function callWithSearch(search: string, paramId: string) {
    const ingestFetch = vi.fn().mockResolvedValue({ transactions: [{ id: 1, amount: 100 }] });
    vi.doMock("@/lib/ingest", () => ({ ingestFetch }));
    const { GET } = await import("@/app/api/cards/[id]/transactions/route");
    const req = new Request(`http://localhost/api/cards/${paramId}/transactions${search}`);
    const res = await GET(req, { params: Promise.resolve({ id: paramId }) });
    const data = await res.json();
    return { ingestFetch, data };
  }

  it("builds the upstream path with default limit", async () => {
    const { ingestFetch } = await callWithSearch("", "42");
    expect(ingestFetch).toHaveBeenCalledWith("/cards/42/transactions?limit=500");
  });

  it("forwards the unbilled period of 'Current Statement' (E2 M7)", async () => {
    const { ingestFetch } = await callWithSearch("?period=unbilled", "3");
    expect(ingestFetch).toHaveBeenCalledWith("/cards/3/transactions?limit=500&period=unbilled");
  });

  it("appends optional period, from, to, limit", async () => {
    const { ingestFetch } = await callWithSearch(
      "?period=2026-05&from=2026-05-01&to=2026-05-31&limit=25",
      "7"
    );
    expect(ingestFetch).toHaveBeenCalledWith(
      "/cards/7/transactions?limit=25&period=2026-05&from=2026-05-01&to=2026-05-31"
    );
  });

  it("answers 502 with empty transactions + the error on ingest fail (E2 M11)", async () => {
    vi.doMock("@/lib/ingest", () => ({
      ingestFetch: vi.fn().mockRejectedValue(new Error("502 upstream")),
    }));
    const { GET } = await import("@/app/api/cards/[id]/transactions/route");
    const res = await GET(new Request("http://localhost/api/cards/1/transactions"), {
      params: Promise.resolve({ id: "1" }),
    });
    expect(res.status).toBe(502);
    const data = await res.json();
    expect(data.transactions).toEqual([]);
    expect(data.error).toBe("502 upstream");
  });
});
