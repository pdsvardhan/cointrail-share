import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

function makeTmpDbPath() {
  return path.join(
    os.tmpdir(),
    `cointrail-migrate-test-${Date.now()}-${Math.random().toString(36).slice(2)}.db`
  );
}

describe("ensureMigrated()", () => {
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

  it("creates all six expected tables", async () => {
    const { ensureMigrated } = await import("@/lib/migrate");
    const { getDb } = await import("@/lib/db");
    ensureMigrated();
    const db = getDb();
    const tables = db
      .prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
      .all()
      .map((r: any) => r.name)
      .filter((n: string) => !n.startsWith("sqlite_"));
    expect(tables).toEqual(
      expect.arrayContaining([
        "categories",
        "cards",
        "expenses",
        "card_statements",
        "recurring",
        "settings",
      ])
    );
  });

  it("creates the four expense indexes", async () => {
    const { ensureMigrated } = await import("@/lib/migrate");
    const { getDb } = await import("@/lib/db");
    ensureMigrated();
    const db = getDb();
    const indexes = db
      .prepare("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='expenses'")
      .all()
      .map((r: any) => r.name);
    expect(indexes).toEqual(
      expect.arrayContaining([
        "idx_expenses_date",
        "idx_expenses_source",
        "idx_expenses_category",
        "idx_expenses_card",
      ])
    );
  });

  it("seeds the 13 default categories", async () => {
    const { ensureMigrated } = await import("@/lib/migrate");
    const { getDb } = await import("@/lib/db");
    ensureMigrated();
    const db = getDb();
    const count = (db.prepare("SELECT COUNT(*) as c FROM categories").get() as any).c;
    expect(count).toBe(13);
    const names = db
      .prepare("SELECT name FROM categories ORDER BY id")
      .all()
      .map((r: any) => r.name);
    expect(names).toContain("Food & Dining");
    expect(names).toContain("EMI & Loans");
    expect(names).toContain("Miscellaneous");
  });

  it("is idempotent — running twice does not duplicate categories", async () => {
    const { ensureMigrated } = await import("@/lib/migrate");
    const { getDb } = await import("@/lib/db");
    ensureMigrated();
    ensureMigrated();
    ensureMigrated();
    const db = getDb();
    const count = (db.prepare("SELECT COUNT(*) as c FROM categories").get() as any).c;
    expect(count).toBe(13);
  });

  it("normalises non-ISO dates inserted into expenses on next migration run", async () => {
    // First migration to create tables
    const { ensureMigrated } = await import("@/lib/migrate");
    const { getDb } = await import("@/lib/db");
    ensureMigrated();
    const db = getDb();

    // Insert rows with non-ISO dates
    const stmt = db.prepare("INSERT INTO expenses (amount, date) VALUES (?, ?)");
    stmt.run(100, "16-05-2026");
    stmt.run(200, "16/May/2026");
    stmt.run(300, "01/01/26");
    stmt.run(400, "2026-12-31"); // already ISO; should pass through

    // Force a fresh module session so ensureMigrated() runs normalizeDates again
    vi.resetModules();
    const { ensureMigrated: ensureMigratedFresh } = await import("@/lib/migrate");
    const { getDb: getDbFresh } = await import("@/lib/db");
    ensureMigratedFresh();
    const db2 = getDbFresh();
    const dates = db2
      .prepare("SELECT date FROM expenses ORDER BY id")
      .all()
      .map((r: any) => r.date);
    expect(dates).toEqual(["2026-05-16", "2026-05-16", "2026-01-01", "2026-12-31"]);
  });

  it("creates settings KV table accepting upserts", async () => {
    const { ensureMigrated } = await import("@/lib/migrate");
    const { getDb } = await import("@/lib/db");
    ensureMigrated();
    const db = getDb();
    db.prepare("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)").run("theme", "dark");
    const row = db.prepare("SELECT value FROM settings WHERE key = ?").get("theme") as any;
    expect(row.value).toBe("dark");
  });
});
