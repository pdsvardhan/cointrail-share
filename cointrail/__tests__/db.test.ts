import { describe, it, expect, beforeEach, afterEach } from "vitest";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

/**
 * lib/db.ts holds a module-level singleton (`db`). We exercise it by:
 *  - Setting DB_PATH to a unique tmp file BEFORE the import
 *  - Using vi.resetModules() to force a fresh import per test
 *  - Tearing down the tmp file afterEach
 *
 * This isolates the singleton without mocking better-sqlite3.
 */

import { vi } from "vitest";

function makeTmpDbPath() {
  return path.join(
    os.tmpdir(),
    `cointrail-db-test-${Date.now()}-${Math.random().toString(36).slice(2)}.db`
  );
}

describe("getDb()", () => {
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

  it("creates the parent directory if missing", async () => {
    const nestedTmp = path.join(
      os.tmpdir(),
      `cointrail-db-test-${Date.now()}`,
      "nested",
      "deep.db"
    );
    process.env.DB_PATH = nestedTmp;
    vi.resetModules();
    const { getDb } = await import("@/lib/db");
    const db = getDb();
    expect(fs.existsSync(path.dirname(nestedTmp))).toBe(true);
    db.close();
    fs.rmSync(path.dirname(path.dirname(nestedTmp)), { recursive: true, force: true });
  });

  it("returns a working better-sqlite3 instance", async () => {
    const { getDb } = await import("@/lib/db");
    const db = getDb();
    const res = db.prepare("SELECT 1 as v").get() as { v: number };
    expect(res.v).toBe(1);
    db.close();
  });

  it("applies WAL journal_mode", async () => {
    const { getDb } = await import("@/lib/db");
    const db = getDb();
    const mode = db.pragma("journal_mode", { simple: true });
    expect(mode).toBe("wal");
    db.close();
  });

  it("applies foreign_keys = ON", async () => {
    const { getDb } = await import("@/lib/db");
    const db = getDb();
    const fk = db.pragma("foreign_keys", { simple: true });
    expect(fk).toBe(1);
    db.close();
  });

  it("applies busy_timeout = 5000", async () => {
    const { getDb } = await import("@/lib/db");
    const db = getDb();
    const bt = db.pragma("busy_timeout", { simple: true });
    expect(bt).toBe(5000);
    db.close();
  });

  it("returns the same singleton instance across calls in one module session", async () => {
    const { getDb } = await import("@/lib/db");
    const a = getDb();
    const b = getDb();
    expect(a).toBe(b);
    a.close();
  });
});
