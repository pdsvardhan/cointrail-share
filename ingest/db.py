"""
Database layer for ingest service.
SQLite with WAL mode for concurrent reads.
Thread-safe via thread-local connections.

AUTOCOMMIT (iter-16, 2026-08-20)
--------------------------------
Connections are opened with isolation_level=None (autocommit). Previously they used
Python's default implicit-transaction mode, where any INSERT/UPDATE/DELETE silently
opens a transaction that is held until an explicit commit(). Because these connections
are thread-local and never closed, a single write path that returned without committing
held SQLite's write lock indefinitely — every other writer then hit busy_timeout and
raised "database is locked". That is exactly what stalled the whole pipeline from
2026-08-12 to 2026-08-19 (1,183 queued tasks, 103,713 lock errors, zero alerts).

Under autocommit a forgotten commit() is harmless: the statement has already been
committed. Existing conn.commit() calls throughout the codebase remain safe — commit()
with no open transaction is a no-op — so no caller needed changing.

Where a group of statements must be all-or-nothing, use the transaction() context
manager below, which takes an explicit BEGIN IMMEDIATE and always ends in COMMIT or
ROLLBACK. Never hand-roll a bare "BEGIN" — that is how the original bug class returns.
"""
import sqlite3
import os
import threading
from contextlib import contextmanager
from config import DB_PATH

_local = threading.local()

# A write may legitimately wait behind a long batch task (email sync, PDF parsing).
# 5s was too short and turned normal contention into hard failures.
BUSY_TIMEOUT_MS = 30000
# Checkpoint the WAL roughly every 1000 pages. The default autocheckpoint was never
# reached while the lock was held, so the WAL grew to 22MB against a 49MB database.
WAL_AUTOCHECKPOINT_PAGES = 1000


def get_db():
    """Get a thread-local SQLite connection in autocommit mode with WAL enabled."""
    if not hasattr(_local, 'conn') or _local.conn is None:
        os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)
        _local.conn = sqlite3.connect(
            DB_PATH,
            isolation_level=None,          # autocommit — see module docstring
            timeout=BUSY_TIMEOUT_MS / 1000,
        )
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
        _local.conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        _local.conn.execute(f"PRAGMA wal_autocheckpoint={WAL_AUTOCHECKPOINT_PAGES}")
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


@contextmanager
def transaction():
    """Explicit all-or-nothing block for multi-statement writes.

    Always exits via COMMIT or ROLLBACK, so a raising body can never leave the write
    lock held. BEGIN IMMEDIATE takes the write lock up front rather than upgrading
    mid-transaction, which avoids deadlocking against a concurrent writer.
    """
    conn = get_db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def checkpoint_wal(mode: str = "PASSIVE") -> dict:
    """Force a WAL checkpoint. Returns the raw checkpoint result for monitoring."""
    conn = get_db()
    row = conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
    return {"busy": row[0], "wal_pages": row[1], "checkpointed_pages": row[2]} if row else {}


def wal_size_bytes() -> int:
    """Size of the -wal sidecar file, or 0 if absent. Used by the health check."""
    try:
        return os.path.getsize(DB_PATH + "-wal")
    except OSError:
        return 0


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT NOT NULL,
            body TEXT NOT NULL,
            phone_timestamp TEXT,
            received_at TEXT NOT NULL DEFAULT (datetime('now')),
            is_financial INTEGER DEFAULT 0,
            is_processed INTEGER DEFAULT 0,
            raw_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sms_received ON sms(received_at);
        CREATE INDEX IF NOT EXISTS idx_sms_financial ON sms(is_financial);
        CREATE INDEX IF NOT EXISTS idx_sms_processed ON sms(is_processed);

        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmail_id TEXT UNIQUE NOT NULL,
            thread_id TEXT,
            sender TEXT NOT NULL,
            sender_email TEXT,
            recipient TEXT,
            subject TEXT,
            body_snippet TEXT,
            date TEXT NOT NULL,
            labels TEXT,
            has_attachments INTEGER DEFAULT 0,
            is_financial INTEGER DEFAULT 0,
            is_processed INTEGER DEFAULT 0,
            raw_json TEXT,
            synced_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_emails_gmail_id ON emails(gmail_id);
        CREATE INDEX IF NOT EXISTS idx_emails_date ON emails(date);
        CREATE INDEX IF NOT EXISTS idx_emails_financial ON emails(is_financial);
        CREATE INDEX IF NOT EXISTS idx_emails_sender ON emails(sender_email);

        CREATE TABLE IF NOT EXISTS email_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id INTEGER REFERENCES emails(id),
            gmail_message_id TEXT,
            attachment_id TEXT,
            filename TEXT,
            mime_type TEXT,
            file_path TEXT,
            size_bytes INTEGER,
            is_parsed INTEGER DEFAULT 0,
            parsed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bank TEXT NOT NULL,
            card_name TEXT,
            last4 TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(bank, last4)
        );

        CREATE TABLE IF NOT EXISTS statements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER REFERENCES cards(id),
            month INTEGER,
            year INTEGER,
            pdf_path TEXT,
            email_id INTEGER REFERENCES emails(id),
            parsed_at TEXT,
            total_amount REAL,
            transaction_count INTEGER,
            status TEXT DEFAULT 'pending',
            UNIQUE(card_id, month, year)
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL,
            raw_description TEXT,
            merchant TEXT,
            category TEXT DEFAULT 'Uncategorized',
            date TEXT NOT NULL,
            source TEXT NOT NULL,
            source_ref TEXT,
            bank TEXT,
            card_last4 TEXT,
            card_id INTEGER REFERENCES cards(id),
            statement_id INTEGER REFERENCES statements(id),
            txn_type TEXT DEFAULT 'debit',
            confidence TEXT DEFAULT 'low',
            is_duplicate INTEGER DEFAULT 0,
            matched_txn_id INTEGER,
            raw_text TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_txn_date ON transactions(date);
        CREATE INDEX IF NOT EXISTS idx_txn_source ON transactions(source);
        CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions(category);
        CREATE INDEX IF NOT EXISTS idx_txn_card ON transactions(card_id);
        CREATE INDEX IF NOT EXISTS idx_txn_amount_date ON transactions(amount, date);
        CREATE INDEX IF NOT EXISTS idx_txn_confidence ON transactions(confidence);

        CREATE TABLE IF NOT EXISTS merchant_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern TEXT NOT NULL UNIQUE,
            match_type TEXT DEFAULT 'contains',
            merchant_name TEXT NOT NULL,
            category TEXT NOT NULL,
            source TEXT DEFAULT 'seed',
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS category_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER REFERENCES transactions(id),
            old_category TEXT,
            new_category TEXT NOT NULL,
            old_merchant TEXT,
            new_merchant TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS pdf_passwords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern TEXT NOT NULL,
            password TEXT NOT NULL,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS upi_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upi_id TEXT,
            name_pattern TEXT,
            mapped_type TEXT NOT NULL DEFAULT 'merchant',
            brand TEXT,
            category TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(upi_id)
        );
        CREATE INDEX IF NOT EXISTS idx_upi_mapping_type ON upi_mappings(mapped_type);

        CREATE TABLE IF NOT EXISTS email_enrichments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id INTEGER REFERENCES emails(id),
            transaction_id INTEGER REFERENCES transactions(id),
            enrichment_type TEXT NOT NULL,
            brand TEXT,
            extracted_data TEXT,
            matched_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_enrichment_txn ON email_enrichments(transaction_id);
        CREATE INDEX IF NOT EXISTS idx_enrichment_type ON email_enrichments(enrichment_type);

    """)
    conn.commit()
    print("Database initialized")


def set_sync_state(key, value):
    conn = get_db()
    conn.execute(
        "INSERT INTO sync_state (key, value, updated_at) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')",
        (key, value)
    )
    conn.commit()


def get_sync_state(key):
    conn = get_db()
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def run_migrations():
    """Run schema migrations and seed data. Safe to call repeatedly."""
    conn = get_db()
    print("Running migrations...")

    # 1. Fix DD/MM/YYYY dates -> YYYY-MM-DD
    cur = conn.execute(
        "UPDATE transactions SET date = substr(date,7,4) || '-' || substr(date,4,2) || '-' || substr(date,1,2) "
        "WHERE date LIKE '__/__/____'"
    )
    if cur.rowcount:
        print(f"  Fixed {cur.rowcount} DD/MM/YYYY dates")

    # 2. Add body_text to emails
    try:
        conn.execute("ALTER TABLE emails ADD COLUMN body_text TEXT")
        print("  Added emails.body_text column")
    except Exception:
        pass

    # 3. Add columns to cards
    for col in ["card_name TEXT", "network TEXT", "billing_group TEXT"]:
        try:
            conn.execute(f"ALTER TABLE cards ADD COLUMN {col}")
            print(f"  Added cards.{col.split()[0]} column")
        except Exception:
            pass

    # 4-5. The owner's card list used to be hard-coded here and re-upserted (active=1) on every
    # startup, which silently reverted any edit and made a replaced card impossible to track.
    # It now lives in the data directory — see sync_cards_config() (iter-18 M10).

    # 6. Add body_html to emails (for Amazon HTML parsing)
    try:
        conn.execute("ALTER TABLE emails ADD COLUMN body_html TEXT")
        print("  Added emails.body_html column")
    except Exception:
        pass

    conn.commit()
    print("Migrations complete")

def run_billing_migrations():
    """Add billing cycle config and cc_payments table."""
    conn = get_db()
    print("Running billing migrations...")

    # 1. Add billing columns to cards
    for col in ["statement_day INTEGER", "due_day INTEGER", "pdf_card_last4 TEXT"]:
        try:
            conn.execute(f"ALTER TABLE cards ADD COLUMN {col}")
            print(f"  Added cards.{col.split()[0]}")
        except Exception:
            pass

    # 2. Add billing_start/billing_end to statements
    for col in ["billing_start TEXT", "billing_end TEXT"]:
        try:
            conn.execute(f"ALTER TABLE statements ADD COLUMN {col}")
            print(f"  Added statements.{col.split()[0]}")
        except Exception:
            pass

    # 3. Create cc_payments table
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cc_payments ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "card_last4 TEXT NOT NULL, "
        "amount REAL NOT NULL, "
        "payment_date TEXT NOT NULL, "
        "source TEXT, "
        "sms_id INTEGER, "
        "raw_text TEXT, "
        "created_at TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cc_payments_card ON cc_payments(card_last4)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cc_payments_date ON cc_payments(payment_date)")
    print("  Created cc_payments table")

    # 4. Billing days (statement_day / due_day / pdf_card_last4) are card configuration and come
    # from sync_cards_config() now, not from a list re-applied over the DB on every startup.

    conn.commit()
    print("Billing migrations complete")


def run_c1_r4_migrations():
    """iter-16 C1 + R4 schema.

    C1: statements gets stated_total (the bank's printed 'Total Amount Due', authoritative
    when known), computed_total (the sum of parsed transactions, a lower bound), and a
    reconciliation flag/variance so an undercounting parse becomes visible instead of
    silently wrong. total_amount is kept as the display value (= stated_total when we
    have it, else computed_total) so existing readers don't break.

    R4: sms_receipts is an append-only audit of every POST to /sms — one row per delivery,
    including ones we reject — so 'did the phone actually send it?' is answerable from the
    server instead of inferred. Body is stored as a SHA-256 prefix, not plaintext.
    """
    conn = get_db()
    print("Running C1+R4 migrations...")
    for col in ["stated_total REAL", "computed_total REAL",
                "needs_review INTEGER DEFAULT 0", "variance_pct REAL",
                "total_source TEXT"]:
        try:
            conn.execute(f"ALTER TABLE statements ADD COLUMN {col}")
            print(f"  Added statements.{col.split()[0]}")
        except Exception:
            pass

    conn.execute(
        "CREATE TABLE IF NOT EXISTS sms_receipts ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "received_at TEXT DEFAULT (datetime('now')), "
        "sender TEXT, "
        "body_sha TEXT, "        # sha256 hex prefix, not the message
        "body_len INTEGER, "
        "is_financial INTEGER, "
        "status TEXT, "          # the response status: processed | duplicate | stored | error
        "sms_id INTEGER, "       # the sms row id (present once store_sms has run)
        "detail TEXT)"           # reason (e.g. non_transaction) / error / message, when present
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_receipts_at ON sms_receipts(received_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_receipts_status ON sms_receipts(status)")
    conn.commit()
    print("C1+R4 migrations complete")


def _add_columns(conn, table, columns):
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for col in columns:
        name = col.split()[0]
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
            print(f"  Added {table}.{name}")


def run_iter18_migrations():
    """iter-18 schema (2026-10-20 audit hardening). Additive only; safe to call repeatedly.

    transactions:
      currency / original_amount / fx_estimated  — HP4: a foreign-currency spend keeps its real
          amount and currency; `amount` stays INR (estimated until the statement line lands).
      kind        — HP4: what the money movement intrinsically is (purchase, refund, reversal,
          cashback, fee, interest, emi_principal, emi_conversion, atm, transfer_in, debit,
          bill_payment). Category says WHERE money went; kind says WHAT happened.
      ref_no      — UPI ref / UTR / bank reference: the stable identity HP3 merges on.
      occurred_at — full timestamp when the source gives one (IST, with offset).
      merge_rule  — why a row was linked as a duplicate (audit trail for the merge rules).
    statements: due_date / due_date_source / min_due — H1: the bank's own due date.
    cc_payments: email_id / ref — M1: payments evidenced by email (CRED, bank) not just SMS.
    cards: replaced_by_card_id — M10: a reissued card links to its successor.
    parse_quarantine — HP1: financial messages no rule could read, held for review instead of
        being silently marked processed.
    """
    conn = get_db()
    print("Running iter-18 migrations...")
    _add_columns(conn, "transactions", [
        "currency TEXT DEFAULT 'INR'",
        "original_amount REAL",
        "fx_estimated INTEGER DEFAULT 0",
        "kind TEXT",
        "ref_no TEXT",
        "occurred_at TEXT",
        "merge_rule TEXT",
    ])
    _add_columns(conn, "statements", [
        "due_date TEXT",
        "due_date_source TEXT",
        "min_due REAL",
    ])
    _add_columns(conn, "cc_payments", ["email_id INTEGER", "ref TEXT"])
    _add_columns(conn, "cards", ["replaced_by_card_id INTEGER"])
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_txn_card_date ON transactions(card_last4, date);
        CREATE INDEX IF NOT EXISTS idx_txn_ref ON transactions(ref_no);
        CREATE INDEX IF NOT EXISTS idx_txn_source_ref ON transactions(source, source_ref);
        CREATE INDEX IF NOT EXISTS idx_txn_matched ON transactions(matched_txn_id);
        CREATE INDEX IF NOT EXISTS idx_txn_kind ON transactions(kind);
        CREATE INDEX IF NOT EXISTS idx_sms_sender_ts ON sms(sender, phone_timestamp);

        CREATE TABLE IF NOT EXISTS parse_quarantine (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            sender TEXT,
            bank TEXT,
            reason TEXT NOT NULL,
            template TEXT,
            excerpt TEXT,
            first_seen TEXT DEFAULT (datetime('now')),
            last_tried TEXT DEFAULT (datetime('now')),
            attempts INTEGER DEFAULT 1,
            resolved_at TEXT,
            resolution TEXT,
            transaction_id INTEGER,
            UNIQUE(source, source_ref)
        );
        CREATE INDEX IF NOT EXISTS idx_quarantine_open ON parse_quarantine(resolved_at);
    """)
    conn.commit()
    print("iter-18 migrations complete")


def run_iter203_be2_migrations():
    """iter-203 schema (categories + recurring). Additive only; safe to call repeatedly.

    payee_rules — E3/E9: a category the owner chose for every payment to one payee. A rule is only
        ever created on request ("apply to all payments to this payee"), never learned silently from
        a single correction. key_type is 'vpa' (UPI ID), 'account' (the counterparty account's
        visible digits, e.g. IMPS "to a/c XXXXXXX999" -> '999') or 'name' (the exact payee text,
        normalised). Owner-specific rules (e.g. a builder's account) are data, never code.
    recurring_cancellations — E5: a recurring series the owner cancelled, keyed by the series_key
        /recurring/detect reports; a charge after cancelled_on raises a warning.
    """
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS payee_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_type TEXT NOT NULL,
            key TEXT NOT NULL,
            label TEXT,
            category TEXT NOT NULL,
            merchant_name TEXT,
            source TEXT DEFAULT 'user',
            created_from_txn_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT,
            UNIQUE(key_type, key)
        );
        CREATE TABLE IF NOT EXISTS recurring_cancellations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_key TEXT NOT NULL UNIQUE,
            display_name TEXT,
            cancelled_on TEXT NOT NULL,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()


def run_iter203_cards_migrations():
    """Iteration 203, lane BE-1 (cards + data). Additive only; safe to call repeatedly.

    cc_payments.stmt_ref — E8: the statement payment line ('<file>#<line>') that evidences a payment.
        A statement's "PAYMENT RECEIVED" line is now a payment report like an SMS or a CRED email; the
        reference keeps two lines of one statement two payments and a re-downloaded copy one.
    statements.printed_* / lines_* / reconcile_* — E12: see services.cards_service.reconcile_statements_logic.
    """
    conn = get_db()
    _add_columns(conn, "cc_payments", ["stmt_ref TEXT"])
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cc_payments_stmt_ref ON cc_payments(stmt_ref)")
    # E12: the statement PDF's printed account summary and the sums of its own lines, so reconcile
    # compares like with like (the bank's purchases with the lines it printed, not a date window),
    # and what reconcile concluded (gap + which comparison it could make).
    _add_columns(conn, "statements", [
        "printed_previous REAL",
        "printed_purchases REAL",
        "printed_credits REAL",
        "lines_charges REAL",
        "lines_credits REAL",
        "lines_emi_principal REAL",
        "reconcile_gap REAL",
        "reconcile_method TEXT",
    ])
    conn.commit()


CARD_FIELDS = ("bank", "last4", "card_name", "network", "billing_group", "statement_day",
               "due_day", "pdf_card_last4", "active", "replaced_by_card_id")


def _cards_config_path():
    from config import CARDS_CONFIG_PATH
    return CARDS_CONFIG_PATH


def export_cards_config(conn=None, path=None):
    """Write the cards table to the config file (the data directory, not the repo)."""
    import json
    conn = conn or get_db()
    path = path or _cards_config_path()
    rows = conn.execute(
        "SELECT " + ", ".join(CARD_FIELDS) + " FROM cards ORDER BY bank, last4"
    ).fetchall()
    cards = [{k: r[k] for k in CARD_FIELDS} for r in rows]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"cards": cards}, f, indent=2)
    os.replace(tmp, path)
    return len(cards)


def sync_cards_config(path=None):
    """iter-18 M10: card identity + billing config lives in a JSON file in the data directory.

    - File missing -> created from the current cards table (first run on an existing install
      keeps every value the owner already had).
    - File present -> each listed card is upserted (by bank+last4). Cards in the DB but not in
      the file are left alone, and nothing is re-activated behind the owner's back.
    Edits made through the API write the file too (export_cards_config), so the two never drift.
    """
    import json
    conn = get_db()
    path = path or _cards_config_path()
    if not os.path.exists(path):
        n = export_cards_config(conn, path)
        print(f"  Cards config created from DB ({n} cards): {path}")
        return {"created": n}
    with open(path, encoding="utf-8") as f:
        cards = json.load(f).get("cards", [])
    applied = 0
    for c in cards:
        if not c.get("bank") or not c.get("last4"):
            continue
        vals = {k: c.get(k) for k in CARD_FIELDS}
        vals["active"] = 1 if vals.get("active") in (None, 1, True, "1") else 0
        conn.execute(
            "INSERT INTO cards (" + ", ".join(CARD_FIELDS) + ") VALUES (" + ", ".join("?" * len(CARD_FIELDS)) + ") "
            "ON CONFLICT(bank, last4) DO UPDATE SET "
            + ", ".join(f"{k}=excluded.{k}" for k in CARD_FIELDS if k not in ("bank", "last4")),
            tuple(vals[k] for k in CARD_FIELDS),
        )
        applied += 1
    conn.commit()
    print(f"  Cards config applied ({applied} cards) from {path}")
    return {"applied": applied}
