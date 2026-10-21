import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

/**
 * lib/ingest.ts reads INGEST_URL + INGEST_API_KEY env vars at import time.
 * We set them via process.env BEFORE vi.resetModules() + dynamic import.
 */

const ORIG_URL = process.env.INGEST_URL;
const ORIG_KEY = process.env.INGEST_API_KEY;

describe("ingestFetch", () => {
  beforeEach(() => {
    process.env.INGEST_URL = "http://test-ingest:9999";
    process.env.INGEST_API_KEY = "test-key-123";
    vi.resetModules();
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    if (ORIG_URL === undefined) delete process.env.INGEST_URL;
    else process.env.INGEST_URL = ORIG_URL;
    if (ORIG_KEY === undefined) delete process.env.INGEST_API_KEY;
    else process.env.INGEST_API_KEY = ORIG_KEY;
  });

  it("calls fetch on INGEST_URL + path", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, where: "test-ingest" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { ingestFetch } = await import("@/lib/ingest");
    const result = await ingestFetch("/cards/summary");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const callArgs = fetchMock.mock.calls[0];
    expect(callArgs[0]).toBe("http://test-ingest:9999/cards/summary");
    expect(result).toEqual({ ok: true, where: "test-ingest" });
  });

  it("includes X-API-Key header when INGEST_API_KEY is set", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);

    const { ingestFetch } = await import("@/lib/ingest");
    await ingestFetch("/cards");

    const init = fetchMock.mock.calls[0][1];
    expect(init.headers["X-API-Key"]).toBe("test-key-123");
    expect(init.headers["Content-Type"]).toBe("application/json");
  });

  it("omits X-API-Key when INGEST_API_KEY is empty", async () => {
    process.env.INGEST_API_KEY = "";
    vi.resetModules();
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);

    const { ingestFetch } = await import("@/lib/ingest");
    await ingestFetch("/cards");

    const init = fetchMock.mock.calls[0][1];
    expect(init.headers["X-API-Key"]).toBeUndefined();
  });

  it("merges caller-supplied headers with defaults", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);

    const { ingestFetch } = await import("@/lib/ingest");
    await ingestFetch("/sms", {
      method: "POST",
      headers: { "X-Custom": "hello" },
      body: JSON.stringify({ msg: "test" }),
    });

    const init = fetchMock.mock.calls[0][1];
    expect(init.headers["X-Custom"]).toBe("hello");
    expect(init.headers["X-API-Key"]).toBe("test-key-123");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ msg: "test" }));
  });

  it("throws when fetch returns !ok with status in the message", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => ({ error: "upstream" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { ingestFetch } = await import("@/lib/ingest");
    await expect(ingestFetch("/cards")).rejects.toThrow("Ingest API error: 502");
  });

  it("returns parsed JSON when response is ok", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ transactions: [{ id: 1, amount: 100 }] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { ingestFetch } = await import("@/lib/ingest");
    const result = await ingestFetch("/transactions");
    expect(result.transactions).toHaveLength(1);
    expect(result.transactions[0].amount).toBe(100);
  });

  it("falls back to default INGEST_URL when env not set", async () => {
    delete process.env.INGEST_URL;
    vi.resetModules();
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);

    const { ingestFetch } = await import("@/lib/ingest");
    await ingestFetch("/cards");
    expect(fetchMock.mock.calls[0][0]).toBe("http://ingest:8600/cards");
  });
});
