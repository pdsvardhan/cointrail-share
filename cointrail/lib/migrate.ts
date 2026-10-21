import { getDb } from "./db";
import { normalizeDate } from "./normalize-date";

let _migrated = false;

export function ensureMigrated() {
  if (_migrated) return;
  runMigrations();
  _migrated = true;
}

function runMigrations() {
  const db = getDb();
  db.exec(`
    CREATE TABLE IF NOT EXISTS categories (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL UNIQUE,
      parent_id INTEGER REFERENCES categories(id),
      icon TEXT
    );
    CREATE TABLE IF NOT EXISTS cards (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      bank TEXT NOT NULL,
      card_name TEXT NOT NULL,
      last4 TEXT,
      reward_type TEXT,
      active INTEGER DEFAULT 1,
      created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS expenses (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      amount REAL NOT NULL,
      category TEXT,
      subcategory TEXT,
      date TEXT NOT NULL,
      description TEXT,
      source TEXT NOT NULL DEFAULT 'manual',
      card_id INTEGER REFERENCES cards(id),
      notes TEXT,
      created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS card_statements (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      card_id INTEGER REFERENCES cards(id),
      month INTEGER NOT NULL,
      year INTEGER NOT NULL,
      pdf_path TEXT,
      parsed_at TEXT,
      total_amount REAL,
      status TEXT DEFAULT 'pending'
    );
    CREATE TABLE IF NOT EXISTS recurring (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      amount REAL NOT NULL,
      category TEXT,
      frequency TEXT DEFAULT 'monthly',
      day_of_month INTEGER DEFAULT 1,
      start_date TEXT NOT NULL,
      active INTEGER DEFAULT 1,
      created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS settings (
      key TEXT PRIMARY KEY,
      value TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date);
    CREATE INDEX IF NOT EXISTS idx_expenses_source ON expenses(source);
    CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenses(category);
    CREATE INDEX IF NOT EXISTS idx_expenses_card ON expenses(card_id);
  `);

  // Seed categories if empty
  const catCount = db.prepare("SELECT COUNT(*) as c FROM categories").get() as any;
  if (catCount.c === 0) {
    const categories = [
      "Food & Dining",
      "Transport",
      "Shopping",
      "Bills & Utilities",
      "Rent & Housing",
      "Health",
      "Entertainment",
      "Travel",
      "Education",
      "Personal",
      "EMI & Loans",
      "Investments",
      "Miscellaneous",
    ];
    const insert = db.prepare("INSERT OR IGNORE INTO categories (name) VALUES (?)");
    for (const cat of categories) {
      insert.run(cat);
    }
  }

  // Normalize any non-ISO dates in expenses
  normalizeDates(db);
}

function normalizeDates(db: any) {
  const ISO_PATTERN = "____-__-__";
  const rows = db
    .prepare(`SELECT id, date FROM expenses WHERE date NOT LIKE '${ISO_PATTERN}'`)
    .all() as Array<{ id: number; date: string }>;

  if (rows.length === 0) return;

  const update = db.prepare("UPDATE expenses SET date = ? WHERE id = ?");
  const txn = db.transaction(() => {
    for (const row of rows) {
      const iso = normalizeDate(row.date);
      if (iso !== row.date) {
        update.run(iso, row.id);
      }
    }
  });
  txn();
}
