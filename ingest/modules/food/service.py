"""
Food-order service — Swiggy / Toing.

Recognizes food-delivery order emails, downloads their invoice PDF attachments
directly from Gmail (by gmail_id), parses them (modules.food.invoice_parser),
and stores structured orders in the `food_invoice_orders` table.

Design note: this deliberately fetches its own attachments rather than piggy-
backing on the core statement-download gate in modules/email/gmail.py (which is
scoped to CC_STATEMENT_SENDERS). That keeps the delicate statement sync path
untouched and lets one code path serve BOTH historical backfill and new orders.
"""
import json
import os
import re

from config import STATEMENTS_DIR
from db import get_db
from modules.food.invoice_parser import parse_food_invoice

# Exact sender address -> platform label.
FOOD_SENDERS = {
    "noreply@swiggy.in": "swiggy",
    "no-reply@swiggy.in": "swiggy",
    "notify@toingit.com": "toing",
}

# Domain fallback -> platform (covers sender-address variants on the same domain).
FOOD_DOMAINS = {
    "swiggy.in": "swiggy",
    "toingit.com": "toing",
}

# Non-restaurant Swiggy verticals to skip cheaply before fetching (groceries).
# The parser's "Restaurant Name:" signature is the real gate, but skipping these
# by subject avoids needless Gmail fetches.
_NON_FOOD_SUBJECT = ("instamart",)

# Where downloaded food invoices are saved (separate from bank statements).
FOOD_INVOICE_DIR = os.path.join(STATEMENTS_DIR, "food")

_ORDER_ID_FROM_FILENAME = re.compile(r"taco[_/](\d+)", re.IGNORECASE)


def platform_for(sender_email):
    """Return 'swiggy' / 'toing' for a food sender, else None."""
    if not sender_email:
        return None
    se = sender_email.lower()
    if se in FOOD_SENDERS:
        return FOOD_SENDERS[se]
    domain = se.split("@")[-1] if "@" in se else ""
    return FOOD_DOMAINS.get(domain)


def is_food_sender(sender_email):
    return platform_for(sender_email) is not None


def ensure_food_schema(conn=None):
    """Create the food_invoice_orders table + indexes if absent (idempotent)."""
    conn = conn or get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS food_invoice_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE,
            platform TEXT NOT NULL,
            restaurant TEXT,
            order_date TEXT,
            total REAL,
            item_count INTEGER,
            items_json TEXT,
            email_id INTEGER,
            gmail_id TEXT,
            source_pdf TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_food_invoice_orders_date ON food_invoice_orders(order_date);
        CREATE INDEX IF NOT EXISTS idx_food_invoice_orders_restaurant ON food_invoice_orders(restaurant);
        CREATE INDEX IF NOT EXISTS idx_food_invoice_orders_platform ON food_invoice_orders(platform);
        """
    )
    conn.commit()


def _candidate_emails(conn):
    """
    Food-sender order emails, newest first.

    Include rows flagged has_attachments=1 OR whose subject says the order was
    delivered. The subject fallback catches senders (e.g. Toing) that were synced
    before being recognized as financial, so their has_attachments flag was never
    set — their invoice PDF is still fetched fresh from Gmail at process time.
    """
    rows = conn.execute(
        "SELECT id, gmail_id, sender_email, subject, date FROM emails "
        "WHERE (lower(sender_email) LIKE '%swiggy.in' OR lower(sender_email) LIKE '%toingit.com') "
        "AND (has_attachments = 1 OR lower(subject) LIKE '%deliver%') "
        "ORDER BY date DESC"
    ).fetchall()
    return [r for r in rows if platform_for(r["sender_email"])]


def _store_order(conn, parsed, platform, email_id, gmail_id, source_pdf):
    """INSERT OR IGNORE by order_id. Returns True if a new row was inserted."""
    order_id = parsed.get("order_id")
    food_items = [it for it in parsed["items"] if not it.get("is_fee")]
    cur = conn.execute(
        "INSERT OR IGNORE INTO food_invoice_orders "
        "(order_id, platform, restaurant, order_date, total, item_count, items_json, "
        " email_id, gmail_id, source_pdf) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            order_id,
            platform,
            parsed.get("restaurant"),
            parsed.get("order_date"),
            parsed.get("total"),
            len(food_items),
            json.dumps(parsed.get("items", []), ensure_ascii=False),
            email_id,
            gmail_id,
            source_pdf,
        ),
    )
    return cur.rowcount > 0


def process_food_orders(limit=None, only_new=True):
    """
    Scan food-sender emails, download + parse their invoice PDFs into food_invoice_orders.

    only_new=True  -> skip emails already represented in food_invoice_orders (hourly path).
    only_new=False -> re-scan all (backfill / retry); INSERT OR IGNORE keeps it idempotent.
    """
    conn = get_db()
    ensure_food_schema(conn)

    # Import lazily so importing this module never requires Gmail creds.
    from modules.email.gmail import get_gmail_service, _find_pdf_attachments

    rows = _candidate_emails(conn)
    if only_new:
        done = {
            r["gmail_id"]
            for r in conn.execute(
                "SELECT gmail_id FROM food_invoice_orders WHERE gmail_id IS NOT NULL"
            ).fetchall()
        }
        rows = [r for r in rows if r["gmail_id"] not in done]
    if limit:
        rows = rows[:limit]

    stats = {
        "scanned": 0, "parsed": 0, "stored": 0,
        "skipped_nonfood": 0, "no_pdf": 0, "parse_fail": 0, "errors": 0,
    }
    if not rows:
        return stats

    service = get_gmail_service()
    os.makedirs(FOOD_INVOICE_DIR, exist_ok=True)

    for r in rows:
        stats["scanned"] += 1
        subject = (r["subject"] or "").lower()
        if any(k in subject for k in _NON_FOOD_SUBJECT):
            stats["skipped_nonfood"] += 1
            continue
        platform = platform_for(r["sender_email"])
        try:
            full = service.users().messages().get(
                userId="me", id=r["gmail_id"], format="full"
            ).execute()
            payload = full.get("payload", {})
        except Exception as e:
            print(f"  [food] fetch error {r['gmail_id']}: {e}")
            stats["errors"] += 1
            continue

        pdfs = _find_pdf_attachments(payload, r["gmail_id"], service)
        if not pdfs:
            stats["no_pdf"] += 1
            continue

        for filename, data in pdfs:
            # Gmail attachment filenames can contain '/' (e.g. "taco/<id>_merged.pdf");
            # flatten to a safe basename before writing.
            safe_name = filename.replace("/", "_").replace("\\", "_")
            out_path = os.path.join(FOOD_INVOICE_DIR, safe_name)
            try:
                with open(out_path, "wb") as f:
                    f.write(data)
            except Exception as e:
                print(f"  [food] save error {filename}: {e}")
                stats["errors"] += 1
                continue

            hint = None
            m = _ORDER_ID_FROM_FILENAME.search(filename)
            if m:
                hint = m.group(1)

            parsed = parse_food_invoice(out_path, order_id_hint=hint)
            if not parsed:
                # Not a restaurant invoice (e.g. Instamart grocery) or unreadable.
                stats["parse_fail"] += 1
                continue
            stats["parsed"] += 1
            try:
                if _store_order(conn, parsed, platform, r["id"], r["gmail_id"], out_path):
                    stats["stored"] += 1
            except Exception as e:
                print(f"  [food] store error {filename}: {e}")
                stats["errors"] += 1

    conn.commit()
    return stats


def get_orders(from_date=None, to_date=None, platform=None, restaurant=None, limit=500):
    """Return stored food orders (flat list, newest first).

    order_type (iter-16 N1)
    -----------------------
    `food_invoice_orders` carries no order_type of its own — an invoice is a restaurant
    tax invoice and does not state how the food was obtained. The legacy `food_orders`
    table does carry one (delivery / dineout / gourmet / instamart), so where the two
    share an order_id we inherit the real value rather than inventing one. It covers
    159 of 303 invoices today.

    The remainder default to 'delivery', which is what an unmatched restaurant tax
    invoice is in practice. That default is reported as `order_type_source: "default"`
    so a consumer can tell an inherited fact from an assumed one instead of treating
    every row as equally certain.

    Note also that quick-commerce (Instamart/Blinkit) never produces a restaurant
    invoice, so it is absent from this table by construction — not missing data.
    """
    conn = get_db()
    ensure_food_schema(conn)
    # A scalar subquery, NOT a LEFT JOIN: food_orders holds duplicate order_id rows
    # (6+ known), so joining multiplied invoices — 303 rows became 308 — which would
    # have silently inflated every total downstream. A subquery can only ever return
    # one value per invoice.
    q = (
        "SELECT i.order_id, i.platform, i.restaurant, i.order_date, i.total, "
        "       i.item_count, i.items_json, "
        "       (SELECT o.order_type FROM food_orders o "
        "         WHERE o.order_id = i.order_id AND o.order_type IS NOT NULL "
        "         LIMIT 1) AS legacy_order_type "
        "FROM food_invoice_orders i "
        "WHERE 1=1"
    )
    p = []
    if from_date:
        q += " AND i.order_date >= ?"
        p.append(from_date)
    if to_date:
        q += " AND i.order_date <= ?"
        p.append(to_date)
    if platform:
        q += " AND i.platform = ?"
        p.append(platform)
    if restaurant:
        q += " AND lower(i.restaurant) LIKE ?"
        p.append(f"%{restaurant.lower()}%")
    q += " ORDER BY i.order_date DESC, i.id DESC LIMIT ?"
    p.append(limit)

    out = []
    for row in conn.execute(q, p).fetchall():
        legacy_type = row["legacy_order_type"]
        out.append(
            {
                "order_id": row["order_id"],
                "platform": row["platform"],
                "restaurant": row["restaurant"],
                "order_date": row["order_date"],
                "total": row["total"],
                "item_count": row["item_count"],
                "order_type": legacy_type or "delivery",
                "order_type_source": "legacy" if legacy_type else "default",
                "items": json.loads(row["items_json"] or "[]"),
            }
        )
    return out
