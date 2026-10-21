"""
Post-enrichment fix-up: link orphan email enrichments to bare transactions
by date proximity, and upgrade merchant strings for already-linked enrichments
that still display the bare brand name.

Runs after the main enrichment pass in the hourly cron. Idempotent.
"""
import html
import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from db import get_db
from modules.email.enrichment import (
    AMOUNT_TOLERANCE_RS, DATE_WINDOW_DAYS, MIN_MATCH_AMOUNT_RS, PROTECTED_CATEGORIES, PROTECTED_KINDS,
    has_manual_override, is_order_email,
)
from util.timez import IST


# Map brand -> (merchant SQL pattern, fallback category). The match window is the same ±1 IST day
# with the email's own amount as enrichment (iter-18 M12); the old 3-14 day windows are gone.
BRAND_TO_MATCH = {
    "Swiggy":           ("%swiggy%",     "Food & Dining"),
    "Swiggy Instamart": ("%instamart%",  "Groceries"),
    "Zomato":           ("%zomato%",     "Food & Dining"),
    "Amazon":           ("%amazon%",     "Shopping"),
    "Flipkart":         ("%flipkart%",   "Shopping"),
    "Uber":             ("%uber%",       "Transport"),
    "BookMyShow":       ("%bookmyshow%", "Entertainment"),
    "RedBus":           ("%redbus%",     "Travel"),
}


def _parse_email_date(s):
    """Normalize an email date string to its IST calendar day, YYYY-MM-DD (iter-18 M8)."""
    if not s:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).strftime("%Y-%m-%d")
    except Exception:
        return None


def _known_amount(extracted):
    """An amount the order email itself states, if the enrichment kept one."""
    for key in ("amount", "order_total"):
        v = (extracted or {}).get(key)
        try:
            if v is not None and float(v) > 0:
                return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _build_enriched_merchant(brand, extracted):
    """Construct a rich merchant string from enrichment data (HTML entities decoded)."""
    label = _build_label(brand, extracted)
    return html.unescape(label) if label else None


def _build_label(brand, extracted):
    if not extracted:
        return None
    if extracted.get("restaurant"):
        return f"{brand} - {extracted['restaurant']}"[:80]
    if extracted.get("products"):
        prods = extracted["products"]
        if isinstance(prods, list) and prods:
            first = prods[0]
            name = first.get("name") if isinstance(first, dict) else str(first)
            if name:
                if len(prods) == 1:
                    return f"{brand} - {name[:60]}"
                return f"{brand} - {name[:40]} +{len(prods)-1} more"
    if extracted.get("items"):
        items = extracted["items"]
        if isinstance(items, list) and items:
            first = items[0]
            name = first.get("name") if isinstance(first, dict) else str(first)
            if name:
                if len(items) == 1:
                    return f"{brand} - {name[:60]}"
                return f"{brand} - {name[:40]} +{len(items)-1} more"
    return None


def link_orphan_enrichments(conn=None):
    """Link enrichments with NULL transaction_id to nearby bare transactions.

    For each brand, scans every orphan enrichment and links it to the ONE unenriched
    matching-merchant transaction within the configured window. Conservative: never overwrites
    an already-linked transaction.

    iter-18 M12 — the same wrong-row problem as enrichment: it used to take the closest of
    several candidates and overwrite the category of whatever it picked. Now several candidates
    (or a stated amount that differs) mean "don't know" and nothing is linked; Finance / Self
    Transfer / bill-payment / incoming-transfer rows are never candidates; rows categorised by
    hand are linked but keep their labels.
    """
    conn = conn or get_db()
    already_enriched = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT transaction_id FROM email_enrichments "
            "WHERE transaction_id IS NOT NULL"
        )
    }

    total_linked = 0
    per_brand = {}

    for brand, (pattern, category) in BRAND_TO_MATCH.items():
        orphans = conn.execute(
            "SELECT en.id, en.email_id, en.extracted_data, e.date AS email_date, e.subject "
            "FROM email_enrichments en "
            "JOIN emails e ON en.email_id = e.id "
            "WHERE en.brand = ? AND en.transaction_id IS NULL "
            "ORDER BY e.date DESC",
            (brand,)
        ).fetchall()

        if not orphans:
            continue

        cat_marks = ",".join("?" * len(PROTECTED_CATEGORIES))
        kind_marks = ",".join("?" * len(PROTECTED_KINDS))
        txns = conn.execute(
            "SELECT id, amount, date, merchant, confidence, COALESCE(fx_estimated, 0) AS fx FROM transactions "
            "WHERE merchant LIKE ? AND is_duplicate = 0 "
            "AND txn_type = 'debit' AND amount > 0 "
            f"AND COALESCE(category, '') NOT IN ({cat_marks}) "
            f"AND COALESCE(kind, '') NOT IN ({kind_marks}) "
            "ORDER BY date DESC",
            (pattern, *PROTECTED_CATEGORIES, *PROTECTED_KINDS)
        ).fetchall()

        txns_by_date = {}
        for t in txns:
            if t["date"]:
                txns_by_date.setdefault(t["date"][:10], []).append(t)

        linked = 0
        for orphan in orphans:
            edate = _parse_email_date(orphan["email_date"])
            if not edate:
                continue
            try:
                base = datetime.strptime(edate, "%Y-%m-%d")
            except Exception:
                continue

            try:
                extracted = json.loads(orphan["extracted_data"])
            except Exception:
                extracted = {}
            stated = _known_amount(extracted)
            # iter-18 M12 (verifier round 2): a link needs the email's own amount, to the rupee, within
            # ±1 IST day — the same rule as enrichment. Merchant name + a multi-day window alone linked
            # a Swiggy food email to an Instamart grocery spend and re-filed it as Food.
            if stated is None or stated < MIN_MATCH_AMOUNT_RS:
                continue
            # only order confirmations (not promotions, surveys, refunds, failed payments)
            if not is_order_email(orphan["subject"] or (extracted or {}).get("email_subject")):
                continue

            candidates = []
            for delta in range(-DATE_WINDOW_DAYS, DATE_WINDOW_DAYS + 1):
                d = (base + timedelta(days=delta)).strftime("%Y-%m-%d")
                for t in txns_by_date.get(d, []):
                    if t["id"] in already_enriched:
                        continue
                    if t["fx"] or abs(float(t["amount"]) - stated) > AMOUNT_TOLERANCE_RS:
                        continue
                    candidates.append(t)

            if len(candidates) != 1:
                continue                      # none, or ambiguous: linking a guess relabels the wrong row
            best_txn = candidates[0]

            conn.execute(
                "UPDATE email_enrichments SET transaction_id = ? WHERE id = ?",
                (best_txn["id"], orphan["id"])
            )

            if not has_manual_override(conn, best_txn["id"], best_txn["confidence"]):
                new_merchant = _build_enriched_merchant(brand, extracted) or brand
                # The brand's fallback category only fills a row that has none: a categorised row
                # (Instamart -> Groceries) keeps its category and gains the order detail.
                conn.execute(
                    "UPDATE transactions SET merchant = ?, "
                    "category = CASE WHEN COALESCE(category, '') IN ('', 'Uncategorized', 'Pass-Through') "
                    "THEN COALESCE(?, category) ELSE category END, confidence = 'enriched' "
                    "WHERE id = ?",
                    (new_merchant, category, best_txn["id"])
                )

            already_enriched.add(best_txn["id"])
            linked += 1

        per_brand[brand] = {"orphans": len(orphans), "linked": linked}
        total_linked += linked

    conn.commit()
    return {"total_linked": total_linked, "per_brand": per_brand}


def upgrade_bare_merchants(conn=None):
    """For transactions already linked to enrichments but still showing the
    bare brand name (e.g. just "Swiggy"), upgrade the merchant string to
    include the rich detail (restaurant / item / product) from the enrichment.

    Never touches rows categorised by hand (confidence 'manual' or a category_overrides entry)
    nor Finance / Self Transfer / bill-payment / incoming-transfer rows (iter-18 M12).
    """
    conn = conn or get_db()
    cat_marks = ",".join("?" * len(PROTECTED_CATEGORIES))
    kind_marks = ",".join("?" * len(PROTECTED_KINDS))
    candidates = conn.execute(
        "SELECT t.id, t.merchant, en.brand, en.extracted_data "
        "FROM transactions t "
        "JOIN email_enrichments en ON en.transaction_id = t.id "
        "WHERE COALESCE(t.confidence, '') != 'manual' "
        "AND NOT EXISTS (SELECT 1 FROM category_overrides co WHERE co.transaction_id = t.id) "
        f"AND COALESCE(t.category, '') NOT IN ({cat_marks}) "
        f"AND COALESCE(t.kind, '') NOT IN ({kind_marks}) "
        "AND t.is_duplicate = 0 "
        "AND ("
        "  t.merchant = en.brand "
        "  OR (en.brand = 'Swiggy Instamart' AND t.merchant IN ('Swiggy', 'Swiggy Instamart'))"
        ")",
        (*PROTECTED_CATEGORIES, *PROTECTED_KINDS)
    ).fetchall()

    upgraded = 0
    for row in candidates:
        try:
            extracted = json.loads(row["extracted_data"])
        except Exception:
            continue
        new_merchant = _build_enriched_merchant(row["brand"], extracted)
        if new_merchant and new_merchant != row["merchant"]:
            conn.execute(
                "UPDATE transactions SET merchant = ? WHERE id = ?",
                (new_merchant, row["id"])
            )
            upgraded += 1

    conn.commit()
    return {"upgraded": upgraded}


def run_relink(conn=None):
    """Main entry point: link orphans then upgrade bare merchants."""
    link_result = link_orphan_enrichments(conn)
    upgrade_result = upgrade_bare_merchants(conn)
    return {"link": link_result, "upgrade": upgrade_result}
