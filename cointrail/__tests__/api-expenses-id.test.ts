import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

function tmp() {
  return path.join(
    os.tmpdir(),
    `cointrail-expenses-id-${Date.now()}-${Math.random().toString(36).slice(2)}.db`
  );
}
function cleanup(p: string) {
  try {
    fs.unlinkSync(p);
  } catch {}
  try {
    fs.unlinkSync(p + "-wal");
  } catch {}
  try {
    fs.unlinkSync(p + "-shm");
  } catch {}
}

describe("PUT /api/expenses/[id]", () => {
  let p: string;
  beforeEach(() => {
    p = tmp();
    process.env.DB_PATH = p;
    vi.resetModules();
  });
  afterEach(() => {
    delete process.env.DB_PATH;
    cleanup(p);
  });

  async function seedRow() {
    const { getDb } = await import("@/lib/db");
    const { ensureMigrated } = await import("@/lib/migrate");
    ensureMigrated();
    const result = getDb()
      .prepare(
        "INSERT INTO expenses (amount, category, date, description, source) VALUES (?, ?, ?, ?, ?)"
      )
      .run(100, "Food & Dining", "2026-05-15", "Original", "manual");
    return result.lastInsertRowid as number;
  }

  it("updates a subset of fields and leaves others intact", async () => {
    const id = await seedRow();
    const { PUT } = await import("@/app/api/expenses/[id]/route");
    const res = await PUT(
      new Request(`http://localhost/api/expenses/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ amount: 250, description: "Updated" }),
      }),
      { params: Promise.resolve({ id: String(id) }) }
    );
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.updated).toBe(1);

    const { getDb } = await import("@/lib/db");
    const row = getDb().prepare("SELECT * FROM expenses WHERE id = ?").get(id) as any;
    expect(row.amount).toBe(250);
    expect(row.description).toBe("Updated");
    expect(row.category).toBe("Food & Dining"); // unchanged
    expect(row.date).toBe("2026-05-15"); // unchanged
  });

  it("returns 400 when no fields are supplied", async () => {
    const id = await seedRow();
    const { PUT } = await import("@/app/api/expenses/[id]/route");
    const res = await PUT(
      new Request(`http://localhost/api/expenses/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      }),
      { params: Promise.resolve({ id: String(id) }) }
    );
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toBe("No fields to update");
  });

  it("returns 404 when the row does not exist", async () => {
    const { ensureMigrated } = await import("@/lib/migrate");
    ensureMigrated();
    const { PUT } = await import("@/app/api/expenses/[id]/route");
    const res = await PUT(
      new Request("http://localhost/api/expenses/99999", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ amount: 10 }),
      }),
      { params: Promise.resolve({ id: "99999" }) }
    );
    expect(res.status).toBe(404);
  });

  it("can null out optional fields by setting them explicitly", async () => {
    const id = await seedRow();
    const { PUT } = await import("@/app/api/expenses/[id]/route");
    await PUT(
      new Request(`http://localhost/api/expenses/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notes: "added a note" }),
      }),
      { params: Promise.resolve({ id: String(id) }) }
    );
    const { getDb } = await import("@/lib/db");
    const row = getDb().prepare("SELECT notes FROM expenses WHERE id = ?").get(id) as any;
    expect(row.notes).toBe("added a note");
  });
});
