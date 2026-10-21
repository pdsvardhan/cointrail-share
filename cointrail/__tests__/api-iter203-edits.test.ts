import { describe, it, expect, beforeEach, vi } from "vitest";

/**
 * Iteration 203 E3 / E5 / E6 / E9: the Next routes behind the new controls. Each forwards to Ingest
 * and keeps the error contract of the read routes (E2 M11): Ingest unreachable or failing = 502
 * with the error, never an empty answer; an Ingest 4xx (unknown id, bad input) is passed through
 * with Ingest's own message so the page can say what went wrong.
 */

type Call = { path: string; init?: RequestInit };

function mockIngest(answer: { status: number; body: any } | Error) {
  const calls: Call[] = [];
  const ingestRequest = vi.fn(async (path: string, init?: RequestInit) => {
    calls.push({ path, init });
    if (answer instanceof Error) throw answer;
    return answer;
  });
  vi.doMock("@/lib/ingest", () => ({ ingestRequest, ingestFetch: vi.fn() }));
  return calls;
}

const params = (id: string) => ({ params: Promise.resolve({ id }) });

beforeEach(() => {
  vi.resetModules();
});

describe("PUT /api/transactions/[id] (E3)", () => {
  async function put(id: string, body: unknown) {
    const { PUT } = await import("@/app/api/transactions/[id]/route");
    return PUT(
      new Request(`http://localhost/api/transactions/${id}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
      params(id)
    );
  }

  it("forwards category + apply_to_payee to Ingest and returns its answer", async () => {
    const calls = mockIngest({
      status: 200,
      body: {
        status: "updated",
        transaction: { id: 12, category: "Household help" },
        relabelled: 3,
      },
    });
    const res = await put("12", { category: "Household help", apply_to_payee: true });
    expect(res.status).toBe(200);
    expect((await res.json()).relabelled).toBe(3);
    expect(calls[0].path).toBe("/transactions/12");
    expect(calls[0].init?.method).toBe("PUT");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      category: "Household help",
      apply_to_payee: true,
    });
  });

  it("refuses a blank category and a non-numeric id without calling Ingest", async () => {
    const calls = mockIngest({ status: 200, body: {} });
    expect((await put("12", { category: "  " })).status).toBe(400);
    expect((await put("12;DROP", { category: "Food & Dining" })).status).toBe(400);
    expect(calls).toHaveLength(0);
  });

  it("passes Ingest's 404 through with its message", async () => {
    mockIngest({ status: 404, body: { detail: "Transaction not found" } });
    const res = await put("999999", { category: "Food & Dining" });
    expect(res.status).toBe(404);
    expect((await res.json()).error).toBe("Transaction not found");
  });

  it("answers 502 when Ingest is unreachable or fails", async () => {
    mockIngest(new Error("connect ECONNREFUSED"));
    const down = await put("12", { category: "Food & Dining" });
    expect(down.status).toBe(502);
    expect((await down.json()).error).toContain("ECONNREFUSED");

    vi.resetModules();
    mockIngest({ status: 500, body: { detail: "boom" } });
    expect((await put("12", { category: "Food & Dining" })).status).toBe(502);
  });
});

describe("GET /api/transactions/[id]/payee (E3)", () => {
  it("forwards the lookup", async () => {
    const calls = mockIngest({
      status: 200,
      body: { transaction_id: 5, payee: { key_type: "vpa", key: "x@ybl", label: "x@ybl" } },
    });
    const { GET } = await import("@/app/api/transactions/[id]/payee/route");
    const res = await GET(new Request("http://localhost/api/transactions/5/payee"), params("5"));
    expect(res.status).toBe(200);
    expect((await res.json()).payee.key).toBe("x@ybl");
    expect(calls[0].path).toBe("/transactions/5/payee");
  });
});

describe("/api/payee-rules (E9)", () => {
  it("GET lists the rules", async () => {
    const calls = mockIngest({ status: 200, body: { rules: [{ id: 1 }], total: 1 } });
    const { GET } = await import("@/app/api/payee-rules/route");
    const res = await GET();
    expect((await res.json()).total).toBe(1);
    expect(calls[0].path).toBe("/payee-rules");
  });

  it("GET answers 502 on an outage, not an empty list", async () => {
    mockIngest(new Error("down"));
    const { GET } = await import("@/app/api/payee-rules/route");
    const res = await GET();
    expect(res.status).toBe(502);
    expect((await res.json()).error).toBe("down");
  });

  it("DELETE removes one rule by id", async () => {
    const calls = mockIngest({ status: 200, body: { status: "deleted", relabelled: 4 } });
    const { DELETE } = await import("@/app/api/payee-rules/[id]/route");
    const res = await DELETE(new Request("http://localhost/api/payee-rules/3"), params("3"));
    expect((await res.json()).relabelled).toBe(4);
    expect(calls[0]).toMatchObject({ path: "/payee-rules/3", init: { method: "DELETE" } });
  });

  it("DELETE of an unknown rule is Ingest's 404", async () => {
    mockIngest({ status: 404, body: { detail: "Payee rule not found" } });
    const { DELETE } = await import("@/app/api/payee-rules/[id]/route");
    const res = await DELETE(new Request("http://localhost/api/payee-rules/77"), params("77"));
    expect(res.status).toBe(404);
  });
});

describe("GET /api/category-choices (E3)", () => {
  it("forwards", async () => {
    const calls = mockIngest({
      status: 200,
      body: { categories: [{ name: "Household help", group: "spend" }], owner_categories: [] },
    });
    const { GET } = await import("@/app/api/category-choices/route");
    const res = await GET();
    expect((await res.json()).categories[0].name).toBe("Household help");
    expect(calls[0].path).toBe("/category-choices");
  });
});

describe("/api/recurring/cancellations (E5)", () => {
  async function post(body: unknown) {
    const { POST } = await import("@/app/api/recurring/cancellations/route");
    return POST(
      new Request("http://localhost/api/recurring/cancellations", {
        method: "POST",
        body: JSON.stringify(body),
      })
    );
  }

  it("POST forwards series_key, cancelled_on and display_name", async () => {
    const calls = mockIngest({
      status: 200,
      body: { status: "cancelled", cancellation: { id: 4 } },
    });
    const res = await post({
      series_key: "sub:linkedin",
      cancelled_on: "2026-10-20",
      display_name: "LinkedIn",
      extra: "ignored",
    });
    expect(res.status).toBe(200);
    expect(calls[0].path).toBe("/recurring/cancellations");
    expect(calls[0].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      series_key: "sub:linkedin",
      cancelled_on: "2026-10-20",
      display_name: "LinkedIn",
    });
  });

  it("POST refuses a missing series key or a malformed date locally", async () => {
    const calls = mockIngest({ status: 200, body: {} });
    expect((await post({ cancelled_on: "2026-10-20" })).status).toBe(400);
    expect((await post({ series_key: "sub:x", cancelled_on: "20/10/2026" })).status).toBe(400);
    expect(calls).toHaveLength(0);
  });

  it("DELETE undoes one cancellation", async () => {
    const calls = mockIngest({ status: 200, body: { status: "restored" } });
    const { DELETE } = await import("@/app/api/recurring/cancellations/[id]/route");
    const res = await DELETE(
      new Request("http://localhost/api/recurring/cancellations/4"),
      params("4")
    );
    expect((await res.json()).status).toBe("restored");
    expect(calls[0]).toMatchObject({
      path: "/recurring/cancellations/4",
      init: { method: "DELETE" },
    });
  });

  it("DELETE with a non-numeric id is a 400", async () => {
    const calls = mockIngest({ status: 200, body: {} });
    const { DELETE } = await import("@/app/api/recurring/cancellations/[id]/route");
    const res = await DELETE(
      new Request("http://localhost/api/recurring/cancellations/x"),
      params("../x")
    );
    expect(res.status).toBe(400);
    expect(calls).toHaveLength(0);
  });
});

describe("GET /api/emi-plans (E6)", () => {
  it("forwards card_last4 and include_completed only", async () => {
    const calls = mockIngest({ status: 200, body: { plans: [], total: 0 } });
    const { GET } = await import("@/app/api/emi-plans/route");
    const res = await GET(
      new Request("http://localhost/api/emi-plans?card_last4=0077&include_completed=true&x=1")
    );
    expect(res.status).toBe(200);
    expect(calls[0].path).toBe("/emi-plans?card_last4=0077&include_completed=true");
  });

  it("answers 502 on an outage", async () => {
    mockIngest(new Error("down"));
    const { GET } = await import("@/app/api/emi-plans/route");
    const res = await GET(new Request("http://localhost/api/emi-plans"));
    expect(res.status).toBe(502);
    expect((await res.json()).plans).toEqual([]);
  });
});

describe("ingestRequest (lib/ingest)", () => {
  it("returns status + body without throwing on an HTTP error", async () => {
    vi.doUnmock("@/lib/ingest");
    vi.resetModules();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ detail: "nope" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const { ingestRequest } = await import("@/lib/ingest");
    await expect(ingestRequest("/payee-rules/9", { method: "DELETE" })).resolves.toEqual({
      status: 404,
      body: { detail: "nope" },
    });
    expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
    vi.unstubAllGlobals();
  });
});
