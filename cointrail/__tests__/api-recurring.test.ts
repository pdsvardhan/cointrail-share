import { describe, it, expect, beforeEach, vi } from "vitest";

/**
 * /api/recurring/detect is the only recurring route the site calls. The local-DB routes
 * (/api/recurring, /api/recurring/[id], /api/recurring/generate) had no caller and were removed
 * in iteration 203 E2 C7, with their tests.
 */

describe("GET /api/recurring/detect (ingest proxy)", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("returns ingest payload when available", async () => {
    vi.doMock("@/lib/ingest", () => ({
      ingestFetch: vi.fn().mockResolvedValue({ recurring: [{ name: "Spotify" }] }),
    }));
    const { GET } = await import("@/app/api/recurring/detect/route");
    const res = await GET();
    const data = await res.json();
    expect(data.recurring[0].name).toBe("Spotify");
  });

  it("answers 502 with the error on ingest failure, so the page shows Retry (E2 M11)", async () => {
    vi.doMock("@/lib/ingest", () => ({
      ingestFetch: vi.fn().mockRejectedValue(new Error("down")),
    }));
    const { GET } = await import("@/app/api/recurring/detect/route");
    const res = await GET();
    expect(res.status).toBe(502);
    const data = await res.json();
    expect(data.recurring).toEqual([]);
    expect(data.error).toBe("down");
  });
});
