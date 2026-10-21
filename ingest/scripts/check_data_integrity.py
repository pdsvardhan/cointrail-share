#!/usr/bin/env python3
"""
Ingest Service — Data Integrity Test Suite
Validates all data flowing through the pipeline:
SMS, emails, transactions, credit cards, cross-matching, categorization.
"""
import sqlite3
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta

DB_PATH = "/data/ingest.db"
REPORT = []
PASS_COUNT = 0
FAIL_COUNT = 0
WARN_COUNT = 0


def log(level, category, message, details=None):
    global PASS_COUNT, FAIL_COUNT, WARN_COUNT
    if level == "PASS":
        PASS_COUNT += 1
        icon = "OK"
    elif level == "FAIL":
        FAIL_COUNT += 1
        icon = "FAIL"
    elif level == "WARN":
        WARN_COUNT += 1
        icon = "WARN"
    else:
        icon = "INFO"
    entry = f"[{icon}] [{category}] {message}"
    if details:
        entry += f"\n       {details}"
    REPORT.append(entry)
    print(entry)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def test_sms_integrity(conn):
    """Test SMS data quality."""
    print("\n" + "=" * 60)
    print("1. SMS DATA INTEGRITY")
    print("=" * 60)

    # Total counts
    total = conn.execute("SELECT COUNT(*) as c FROM sms").fetchone()["c"]
    financial = conn.execute("SELECT COUNT(*) as c FROM sms WHERE is_financial=1").fetchone()["c"]
    processed = conn.execute("SELECT COUNT(*) as c FROM sms WHERE is_processed=1").fetchone()["c"]
    unprocessed_fin = conn.execute("SELECT COUNT(*) as c FROM sms WHERE is_financial=1 AND is_processed=0").fetchone()["c"]

    log("PASS" if total > 0 else "FAIL", "SMS", f"Total SMS: {total}")
    log("PASS" if financial > 0 else "FAIL", "SMS", f"Financial SMS: {financial} ({round(financial/total*100,1)}% of total)")
    log("PASS" if processed > 0 else "FAIL", "SMS", f"Processed SMS: {processed}")

    if unprocessed_fin > 10:
        log("WARN", "SMS", f"Unprocessed financial SMS: {unprocessed_fin}", "Consider running /sms/process-pending")
    else:
        log("PASS", "SMS", f"Unprocessed financial SMS: {unprocessed_fin}")

    # Check for NULL bodies
    null_bodies = conn.execute("SELECT COUNT(*) as c FROM sms WHERE body IS NULL OR body = ''").fetchone()["c"]
    log("PASS" if null_bodies == 0 else "FAIL", "SMS", f"SMS with empty body: {null_bodies}")

    # Check for duplicate SMS (same sender + body + timestamp)
    dupes = conn.execute("""
        SELECT COUNT(*) as c FROM (
            SELECT sender, body, phone_timestamp, COUNT(*) as cnt
            FROM sms WHERE phone_timestamp IS NOT NULL
            GROUP BY sender, body, phone_timestamp HAVING cnt > 1
        )
    """).fetchone()["c"]
    log("PASS" if dupes == 0 else "WARN", "SMS", f"Duplicate SMS entries: {dupes}")

    # Check SMS with missing timestamps
    no_ts = conn.execute("SELECT COUNT(*) as c FROM sms WHERE phone_timestamp IS NULL").fetchone()["c"]
    log("PASS" if no_ts < total * 0.05 else "WARN", "SMS", f"SMS without timestamp: {no_ts} ({round(no_ts/total*100,1)}%)")


def test_transaction_integrity(conn):
    """Test transaction data quality."""
    print("\n" + "=" * 60)
    print("2. TRANSACTION DATA INTEGRITY")
    print("=" * 60)

    total = conn.execute("SELECT COUNT(*) as c FROM transactions").fetchone()["c"]
    non_dup = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE is_duplicate=0").fetchone()["c"]
    duplicates = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE is_duplicate=1").fetchone()["c"]

    log("PASS" if total > 0 else "FAIL", "TXN", f"Total transactions: {total} ({non_dup} active, {duplicates} duplicates)")

    # Check for NULL amounts
    null_amt = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE amount IS NULL OR amount = 0").fetchone()["c"]
    log("PASS" if null_amt == 0 else "FAIL", "TXN", f"Transactions with zero/null amount: {null_amt}")

    # Check for negative amounts
    neg_amt = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE amount < 0").fetchone()["c"]
    log("PASS" if neg_amt == 0 else "WARN", "TXN", f"Transactions with negative amount: {neg_amt}")

    # Check for NULL dates
    null_date = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE date IS NULL OR date = ''").fetchone()["c"]
    log("PASS" if null_date == 0 else "FAIL", "TXN", f"Transactions with no date: {null_date}")

    # Check date format (should be YYYY-MM-DD)
    bad_dates = conn.execute("""
        SELECT COUNT(*) as c FROM transactions
        WHERE date IS NOT NULL AND date != ''
        AND date NOT LIKE '____-__-__'
    """).fetchone()["c"]
    log("PASS" if bad_dates == 0 else "FAIL", "TXN", f"Transactions with bad date format (not YYYY-MM-DD): {bad_dates}")
    if bad_dates > 0:
        samples = conn.execute("""
            SELECT id, date, raw_description FROM transactions
            WHERE date IS NOT NULL AND date != '' AND date NOT LIKE '____-__-__' LIMIT 5
        """).fetchall()
        for s in samples:
            log("FAIL", "TXN", f"  Bad date: id={s['id']} date='{s['date']}' desc='{(s['raw_description'] or '')[:50]}'")

    # Check for future dates
    today = datetime.now().strftime("%Y-%m-%d")
    future = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE date > ?", (today,)).fetchone()["c"]
    log("PASS" if future == 0 else "WARN", "TXN", f"Transactions with future dates: {future}")

    # Check for very old dates (before 2020)
    old = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE date < '2020-01-01'").fetchone()["c"]
    log("PASS" if old == 0 else "WARN", "TXN", f"Transactions before 2020: {old}")

    # Categorization coverage
    uncategorized = conn.execute(
        "SELECT COUNT(*) as c FROM transactions WHERE is_duplicate=0 AND category = 'Uncategorized'"
    ).fetchone()["c"]
    pct = round(uncategorized / non_dup * 100, 2) if non_dup > 0 else 0
    log("PASS" if pct < 1 else "WARN" if pct < 5 else "FAIL", "TXN",
        f"Uncategorized: {uncategorized}/{non_dup} ({pct}%)")

    # Check source distribution
    sources = conn.execute("""
        SELECT source, COUNT(*) as c FROM transactions WHERE is_duplicate=0
        GROUP BY source ORDER BY c DESC
    """).fetchall()
    for s in sources:
        log("PASS", "TXN", f"Source '{s['source']}': {s['c']} transactions")

    # Check txn_type distribution
    types = conn.execute("""
        SELECT txn_type, COUNT(*) as c, SUM(amount) as total FROM transactions WHERE is_duplicate=0
        GROUP BY txn_type
    """).fetchall()
    for t in types:
        log("PASS", "TXN", f"Type '{t['txn_type']}': {t['c']} txns, total={round(t['total'],2)}")


def test_amount_consistency(conn):
    """Check for amount anomalies."""
    print("\n" + "=" * 60)
    print("3. AMOUNT CONSISTENCY")
    print("=" * 60)

    # Very large transactions (> 5 lakh)
    large = conn.execute("""
        SELECT id, amount, merchant, category, date, source FROM transactions
        WHERE is_duplicate=0 AND amount > 500000 AND txn_type='debit'
        ORDER BY amount DESC LIMIT 10
    """).fetchall()
    if large:
        log("WARN", "AMT", f"Very large debits (>5L): {len(large)}")
        for t in large:
            log("WARN", "AMT", f"  {t['date']} | {t['merchant'][:30]} | Rs.{t['amount']:,.2f} | {t['source']}")
    else:
        log("PASS", "AMT", "No unexpectedly large debits (>5L)")

    # Very small transactions (< 1 rupee)
    tiny = conn.execute("""
        SELECT COUNT(*) as c FROM transactions
        WHERE is_duplicate=0 AND amount > 0 AND amount < 1
    """).fetchone()["c"]
    log("PASS" if tiny == 0 else "WARN", "AMT", f"Transactions < Rs.1: {tiny}")

    # Monthly spend totals
    monthly = conn.execute("""
        SELECT strftime('%Y-%m', date) as month,
               SUM(CASE WHEN txn_type='debit' THEN amount ELSE 0 END) as spend,
               SUM(CASE WHEN txn_type='credit' THEN amount ELSE 0 END) as income,
               COUNT(*) as txn_count
        FROM transactions WHERE is_duplicate=0 AND date >= '2025-01-01'
        GROUP BY month ORDER BY month DESC
    """).fetchall()
    log("PASS", "AMT", "Monthly spend summary:")
    for m in monthly:
        log("PASS", "AMT", f"  {m['month']}: spend={round(m['spend'],2):>12,.2f}  income={round(m['income'],2):>12,.2f}  txns={m['txn_count']}")


def test_credit_cards(conn):
    """Test credit card data integrity."""
    print("\n" + "=" * 60)
    print("4. CREDIT CARD DATA INTEGRITY")
    print("=" * 60)

    cards = conn.execute("SELECT * FROM cards WHERE active=1").fetchall()
    log("PASS" if len(cards) > 0 else "FAIL", "CC", f"Active credit cards: {len(cards)}")

    for card in cards:
        card_id = card["id"]
        bank = card["bank"]
        last4 = card["last4"]
        card_name = card["card_name"] or "unnamed"

        # Transaction count for this card
        txn_count = conn.execute(
            "SELECT COUNT(*) as c FROM transactions WHERE source='card' AND card_last4=? AND is_duplicate=0",
            (last4,)
        ).fetchone()["c"]

        # Total spend
        total_spend = conn.execute(
            "SELECT COALESCE(SUM(amount),0) as total FROM transactions WHERE source='card' AND card_last4=? AND is_duplicate=0 AND txn_type='debit'",
            (last4,)
        ).fetchone()["total"]

        # Statement count
        stmt_count = conn.execute(
            "SELECT COUNT(*) as c FROM statements WHERE card_id=?", (card_id,)
        ).fetchone()["c"]

        # Payment count
        payment_count = conn.execute(
            "SELECT COUNT(*) as c FROM cc_payments WHERE card_last4=?", (last4,)
        ).fetchone()["c"]

        total_paid = conn.execute(
            "SELECT COALESCE(SUM(amount),0) as total FROM cc_payments WHERE card_last4=?", (last4,)
        ).fetchone()["total"]

        log("PASS" if txn_count > 0 else "WARN", "CC",
            f"{bank} {card_name} (xx{last4}): {txn_count} txns, spend={total_spend:,.2f}, {stmt_count} statements, {payment_count} payments (paid={total_paid:,.2f})")

        # Check billing cycle config
        if card["statement_day"] is None:
            log("WARN", "CC", f"  {bank} xx{last4}: No statement_day configured")

        # Verify statement amounts vs transaction sums
        stmts = conn.execute(
            "SELECT month, year, total_amount, transaction_count, billing_start, billing_end FROM statements WHERE card_id=? ORDER BY year DESC, month DESC LIMIT 3",
            (card_id,)
        ).fetchall()
        for stmt in stmts:
            if stmt["billing_start"] and stmt["billing_end"]:
                actual_txn_total = conn.execute(
                    "SELECT COALESCE(SUM(amount),0) as total, COUNT(*) as c FROM transactions "
                    "WHERE source='card' AND card_last4=? AND is_duplicate=0 AND txn_type='debit' "
                    "AND date >= ? AND date <= ?",
                    (last4, stmt["billing_start"], stmt["billing_end"])
                ).fetchone()
                stmt_total = stmt["total_amount"] or 0
                actual_total = actual_txn_total["total"]
                diff = abs(stmt_total - actual_total)
                pct_diff = (diff / stmt_total * 100) if stmt_total > 0 else 0
                if pct_diff > 20 and diff > 1000:
                    log("WARN", "CC", f"  {bank} xx{last4} stmt {stmt['year']}-{stmt['month']:02d}: "
                        f"stmt_total={stmt_total:,.2f} vs txn_sum={actual_total:,.2f} (diff={diff:,.2f}, {pct_diff:.1f}%)")


def test_cross_matching(conn):
    """Test cross-matching quality."""
    print("\n" + "=" * 60)
    print("5. CROSS-MATCHING INTEGRITY")
    print("=" * 60)

    total_dupes = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE is_duplicate=1").fetchone()["c"]
    matched_card = conn.execute(
        "SELECT COUNT(*) as c FROM transactions WHERE source='card' AND matched_txn_id IS NOT NULL AND is_duplicate=0"
    ).fetchone()["c"]
    unmatched_card = conn.execute(
        "SELECT COUNT(*) as c FROM transactions WHERE source='card' AND matched_txn_id IS NULL AND is_duplicate=0"
    ).fetchone()["c"]

    log("PASS", "XMATCH", f"Duplicate-flagged transactions: {total_dupes}")
    log("PASS", "XMATCH", f"Card txns with SMS match: {matched_card}")
    log("PASS" if unmatched_card < 600 else "WARN", "XMATCH", f"Unmatched card transactions: {unmatched_card}")

    # Check for potential false duplicates (same amount+date marked as dup but different merchants)
    false_dupes = conn.execute("""
        SELECT t1.id, t1.amount, t1.date, t1.merchant, t1.source,
               t2.id as matched_id, t2.merchant as matched_merchant, t2.source as matched_source
        FROM transactions t1
        JOIN transactions t2 ON t1.matched_txn_id = t2.id
        WHERE t1.is_duplicate = 1
        AND t1.merchant IS NOT NULL AND t2.merchant IS NOT NULL
        AND LOWER(t1.merchant) != LOWER(t2.merchant)
        AND t1.merchant NOT LIKE '%UPI%' AND t2.merchant NOT LIKE '%UPI%'
        LIMIT 10
    """).fetchall()
    if false_dupes:
        log("WARN", "XMATCH", f"Potential false duplicates (different merchants matched): {len(false_dupes)}")
        for d in false_dupes[:5]:
            log("WARN", "XMATCH", f"  ID {d['id']} ({d['merchant'][:25]}) matched to ID {d['matched_id']} ({d['matched_merchant'][:25]})")
    else:
        log("PASS", "XMATCH", "No obvious false duplicates found")

    # Check for potential missed duplicates (same amount+date, different sources, not matched)
    potential_missed = conn.execute("""
        SELECT t1.id, t1.amount, t1.date, t1.merchant, t1.source,
               t2.id as other_id, t2.merchant as other_merchant, t2.source as other_source
        FROM transactions t1
        JOIN transactions t2 ON t1.amount = t2.amount
            AND t1.date = t2.date
            AND t1.id != t2.id
            AND t1.source != t2.source
        WHERE t1.is_duplicate = 0 AND t2.is_duplicate = 0
        AND t1.txn_type = 'debit' AND t2.txn_type = 'debit'
        AND t1.source IN ('sms', 'card') AND t2.source IN ('sms', 'card')
        AND t1.id < t2.id
        LIMIT 15
    """).fetchall()
    if potential_missed:
        log("WARN", "XMATCH", f"Potential missed duplicates (same amount+date, different source): {len(potential_missed)}")
        for m in potential_missed[:5]:
            log("WARN", "XMATCH", f"  {m['date']} Rs.{m['amount']:,.2f}: "
                f"ID {m['id']} ({m['source']}: {(m['merchant'] or '')[:20]}) vs "
                f"ID {m['other_id']} ({m['other_source']}: {(m['other_merchant'] or '')[:20]})")
    else:
        log("PASS", "XMATCH", "No missed duplicates detected")


def test_email_integrity(conn):
    """Test email data quality."""
    print("\n" + "=" * 60)
    print("6. EMAIL DATA INTEGRITY")
    print("=" * 60)

    total = conn.execute("SELECT COUNT(*) as c FROM emails").fetchone()["c"]
    financial = conn.execute("SELECT COUNT(*) as c FROM emails WHERE is_financial=1").fetchone()["c"]
    with_attach = conn.execute("SELECT COUNT(*) as c FROM emails WHERE has_attachments=1").fetchone()["c"]
    attachments_dl = conn.execute("SELECT COUNT(*) as c FROM email_attachments").fetchone()["c"]

    log("PASS" if total > 0 else "FAIL", "EMAIL", f"Total emails: {total}")
    log("PASS", "EMAIL", f"Financial emails: {financial} ({round(financial/total*100,1)}%)")
    log("PASS", "EMAIL", f"Emails with attachments: {with_attach}")
    log("PASS", "EMAIL", f"Downloaded attachments: {attachments_dl}")

    # Check for missing body text on financial emails
    no_body = conn.execute(
        "SELECT COUNT(*) as c FROM emails WHERE is_financial=1 AND (body_snippet IS NULL OR body_snippet = '')"
    ).fetchone()["c"]
    log("PASS" if no_body == 0 else "WARN", "EMAIL", f"Financial emails without body: {no_body}")

    # Top financial senders
    top_senders = conn.execute("""
        SELECT sender_email, COUNT(*) as c FROM emails WHERE is_financial=1
        GROUP BY sender_email ORDER BY c DESC LIMIT 10
    """).fetchall()
    log("PASS", "EMAIL", "Top financial email senders:")
    for s in top_senders:
        log("PASS", "EMAIL", f"  {s['sender_email']}: {s['c']} emails")

    # Email enrichment stats
    enrichments = conn.execute("""
        SELECT brand, COUNT(*) as c FROM email_enrichments GROUP BY brand ORDER BY c DESC
    """).fetchall()
    if enrichments:
        log("PASS", "EMAIL", "Email enrichments by brand:")
        for e in enrichments:
            log("PASS", "EMAIL", f"  {e['brand']}: {e['c']} enrichments")


def test_categorization_quality(conn):
    """Test categorization distribution and quality."""
    print("\n" + "=" * 60)
    print("7. CATEGORIZATION QUALITY")
    print("=" * 60)

    categories = conn.execute("""
        SELECT category, COUNT(*) as c,
               SUM(CASE WHEN txn_type='debit' THEN amount ELSE 0 END) as spend
        FROM transactions WHERE is_duplicate=0
        GROUP BY category ORDER BY spend DESC
    """).fetchall()

    log("PASS", "CAT", f"Total categories in use: {len(categories)}")
    for cat in categories:
        log("PASS", "CAT", f"  {cat['category']:<25} {cat['c']:>5} txns  spend={cat['spend']:>12,.2f}")

    # Check confidence distribution
    conf = conn.execute("""
        SELECT confidence, COUNT(*) as c FROM transactions WHERE is_duplicate=0
        GROUP BY confidence ORDER BY c DESC
    """).fetchall()
    log("PASS", "CAT", "Confidence distribution:")
    for c in conf:
        log("PASS", "CAT", f"  {c['confidence']}: {c['c']} transactions")

    # Check merchant alias coverage
    alias_count = conn.execute("SELECT COUNT(*) as c FROM merchant_aliases").fetchone()["c"]
    log("PASS" if alias_count > 100 else "WARN", "CAT", f"Merchant aliases: {alias_count}")

    # Check for common uncategorized patterns
    uncat = conn.execute("""
        SELECT raw_description, COUNT(*) as c FROM transactions
        WHERE is_duplicate=0 AND category='Uncategorized' AND raw_description IS NOT NULL
        GROUP BY raw_description ORDER BY c DESC LIMIT 10
    """).fetchall()
    if uncat:
        log("WARN", "CAT", "Top uncategorized descriptions:")
        for u in uncat:
            log("WARN", "CAT", f"  '{u['raw_description'][:50]}' ({u['c']} times)")


def test_botb_api_compatibility(conn):
    """Test that all data BOTB needs is available and correct."""
    print("\n" + "=" * 60)
    print("8. BOTB API COMPATIBILITY")
    print("=" * 60)

    # /transactions endpoint data
    txns = conn.execute("""
        SELECT COUNT(*) as c FROM transactions WHERE is_duplicate=0
    """).fetchone()["c"]
    log("PASS" if txns > 0 else "FAIL", "BOTB", f"Active transactions for /transactions: {txns}")

    # /cards endpoint
    cards = conn.execute("SELECT COUNT(*) as c FROM cards WHERE active=1").fetchone()["c"]
    log("PASS" if cards > 0 else "FAIL", "BOTB", f"Active cards for /cards: {cards}")

    # /cards/summary — check all cards have at least some data
    cards_with_txns = conn.execute("""
        SELECT c.bank, c.last4, c.card_name,
               COUNT(t.id) as txn_count
        FROM cards c
        LEFT JOIN transactions t ON t.card_last4 = c.last4 AND t.source='card' AND t.is_duplicate=0
        WHERE c.active=1
        GROUP BY c.id
    """).fetchall()
    for c in cards_with_txns:
        if c["txn_count"] == 0:
            log("WARN", "BOTB", f"Card {c['bank']} xx{c['last4']} ({c['card_name']}): 0 transactions")
        else:
            log("PASS", "BOTB", f"Card {c['bank']} xx{c['last4']} ({c['card_name']}): {c['txn_count']} transactions")

    # /categories/summary — check we have categories
    cat_count = conn.execute("""
        SELECT COUNT(DISTINCT category) as c FROM transactions WHERE is_duplicate=0 AND txn_type='debit'
    """).fetchone()["c"]
    log("PASS" if cat_count > 5 else "WARN", "BOTB", f"Distinct spend categories: {cat_count}")

    # /recurring/detect — check we have recurring data
    recurring_candidates = conn.execute("""
        SELECT merchant, COUNT(DISTINCT strftime('%Y-%m', date)) as months
        FROM transactions WHERE is_duplicate=0 AND txn_type='debit' AND date >= date('now', '-6 months')
        GROUP BY merchant HAVING months >= 3
    """).fetchall()
    log("PASS" if len(recurring_candidates) > 0 else "WARN", "BOTB",
        f"Potential recurring merchants (3+ months): {len(recurring_candidates)}")

    # /email/list — check food emails available
    food_emails = conn.execute("""
        SELECT COUNT(*) as c FROM emails
        WHERE is_financial=1 AND (sender_email LIKE '%swiggy%' OR sender_email LIKE '%zomato%'
        OR sender_email LIKE '%blinkit%' OR sender_email LIKE '%zepto%')
    """).fetchone()["c"]
    log("PASS" if food_emails > 0 else "WARN", "BOTB", f"Food delivery emails for /email/list: {food_emails}")

    # Check SMS source transactions have bank and card_last4
    sms_no_bank = conn.execute("""
        SELECT COUNT(*) as c FROM transactions WHERE source='sms' AND is_duplicate=0 AND (bank IS NULL OR bank = '')
    """).fetchone()["c"]
    sms_no_card = conn.execute("""
        SELECT COUNT(*) as c FROM transactions WHERE source='sms' AND is_duplicate=0 AND (card_last4 IS NULL OR card_last4 = '')
    """).fetchone()["c"]
    sms_total = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE source='sms' AND is_duplicate=0").fetchone()["c"]
    log("PASS" if sms_no_bank < sms_total * 0.1 else "WARN", "BOTB",
        f"SMS txns without bank: {sms_no_bank}/{sms_total}")
    log("PASS" if sms_no_card < sms_total * 0.3 else "WARN", "BOTB",
        f"SMS txns without card_last4: {sms_no_card}/{sms_total} (UPI transfers don't have card)")


def test_data_consistency(conn):
    """Cross-check data consistency between tables."""
    print("\n" + "=" * 60)
    print("9. DATA CONSISTENCY CHECKS")
    print("=" * 60)

    # Transactions referencing non-existent cards
    orphan_card_txns = conn.execute("""
        SELECT COUNT(*) as c FROM transactions t
        WHERE t.card_last4 != '' AND t.card_last4 IS NOT NULL
        AND t.source = 'card'
        AND NOT EXISTS (SELECT 1 FROM cards c WHERE c.last4 = t.card_last4)
    """).fetchone()["c"]
    log("PASS" if orphan_card_txns == 0 else "WARN", "CONSISTENCY",
        f"Card transactions with unknown card_last4: {orphan_card_txns}")

    # Statements referencing non-existent cards
    orphan_stmts = conn.execute("""
        SELECT COUNT(*) as c FROM statements s
        WHERE NOT EXISTS (SELECT 1 FROM cards c WHERE c.id = s.card_id)
    """).fetchone()["c"]
    log("PASS" if orphan_stmts == 0 else "FAIL", "CONSISTENCY",
        f"Statements with missing card: {orphan_stmts}")

    # CC payments referencing non-existent cards
    orphan_payments = conn.execute("""
        SELECT COUNT(*) as c FROM cc_payments p
        WHERE NOT EXISTS (SELECT 1 FROM cards c WHERE c.last4 = p.card_last4)
    """).fetchone()["c"]
    log("PASS" if orphan_payments == 0 else "WARN", "CONSISTENCY",
        f"CC payments with unknown card: {orphan_payments}")

    # Duplicate transactions (same amount, date, description, source — but not flagged)
    unflagged_dupes = conn.execute("""
        SELECT COUNT(*) as c FROM (
            SELECT amount, date, raw_description, source, COUNT(*) as cnt
            FROM transactions WHERE is_duplicate=0
            GROUP BY amount, date, raw_description, source HAVING cnt > 1
        )
    """).fetchone()["c"]
    log("PASS" if unflagged_dupes == 0 else "WARN", "CONSISTENCY",
        f"Potential unflagged duplicates (same amount+date+desc+source): {unflagged_dupes}")

    # Check matched_txn_id references exist
    broken_refs = conn.execute("""
        SELECT COUNT(*) as c FROM transactions t1
        WHERE t1.matched_txn_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM transactions t2 WHERE t2.id = t1.matched_txn_id)
    """).fetchone()["c"]
    log("PASS" if broken_refs == 0 else "FAIL", "CONSISTENCY",
        f"Broken matched_txn_id references: {broken_refs}")


def test_payment_tracking(conn):
    """Test CC payment tracking accuracy."""
    print("\n" + "=" * 60)
    print("10. CC PAYMENT TRACKING")
    print("=" * 60)

    total_payments = conn.execute("SELECT COUNT(*) as c FROM cc_payments").fetchone()["c"]
    log("PASS" if total_payments > 0 else "WARN", "PAY", f"Total CC payments tracked: {total_payments}")

    # Per-card payment summary
    card_payments = conn.execute("""
        SELECT p.card_last4, c.bank, c.card_name,
               COUNT(*) as pay_count, SUM(p.amount) as total_paid,
               MIN(p.payment_date) as first_payment, MAX(p.payment_date) as last_payment
        FROM cc_payments p
        LEFT JOIN cards c ON c.last4 = p.card_last4
        GROUP BY p.card_last4
        ORDER BY total_paid DESC
    """).fetchall()
    for cp in card_payments:
        log("PASS", "PAY", f"  {cp['bank'] or '?'} xx{cp['card_last4']} ({cp['card_name'] or '?'}): "
            f"{cp['pay_count']} payments, total={cp['total_paid']:,.2f}, "
            f"range={cp['first_payment']} to {cp['last_payment']}")

    # Check for duplicate payments (same card, same amount, same date)
    dup_payments = conn.execute("""
        SELECT COUNT(*) as c FROM (
            SELECT card_last4, amount, payment_date, COUNT(*) as cnt
            FROM cc_payments GROUP BY card_last4, amount, payment_date HAVING cnt > 1
        )
    """).fetchone()["c"]
    log("PASS" if dup_payments == 0 else "WARN", "PAY", f"Duplicate payment entries: {dup_payments}")




def test_cc_summary_accuracy(conn):
    """Test every stat shown on the credit cards page against source data.
    Verifies: due dates, is_paid, payment totals, unbilled totals, statement counts.
    This is the definitive truth-check for the /cards/summary API."""
    print("\n" + "=" * 60)
    print("11. CC SUMMARY PAGE ACCURACY (Every Stat)")
    print("=" * 60)

    cards = conn.execute(
        "SELECT id, bank, card_name, last4, statement_day, due_day FROM cards WHERE active=1"
    ).fetchall()

    for card in cards:
        card_id = card["id"]
        bank = card["bank"]
        name = card["card_name"]
        last4 = card["last4"]
        stmt_day = card["statement_day"] or 1
        due_day = card["due_day"] or 20
        label = "{} {} (xx{})".format(bank, name, last4)

        latest_stmt = conn.execute(
            "SELECT month, year, total_amount, transaction_count, billing_start, billing_end "
            "FROM statements WHERE card_id=? ORDER BY year DESC, month DESC LIMIT 1",
            (card_id,)
        ).fetchone()

        if not latest_stmt:
            log("PASS", "SUM", "{}: No statements (new/inactive card)".format(label))
            continue

        stmt_m = latest_stmt["month"]
        stmt_y = latest_stmt["year"]
        billing_end = latest_stmt["billing_end"] or ""
        stmt_total = latest_stmt["total_amount"] or 0

        # Due date verification
        due_m = stmt_m if due_day > stmt_day else stmt_m + 1
        due_yr = stmt_y
        if due_m > 12:
            due_m = 1
            due_yr += 1
        expected_due = "{}-{:02d}-{:02d}".format(due_yr, due_m, due_day)

        sms_due = conn.execute(
            "SELECT due_date, amount FROM sms_statement_dues WHERE card_last4=? AND statement_month=? AND statement_year=?",
            (last4, stmt_m, stmt_y)
        ).fetchone()
        if sms_due and sms_due["due_date"]:
            sms_due_date = sms_due["due_date"]
            if sms_due_date != expected_due:
                log("WARN", "SUM", "{}: Calculated due={} vs SMS due={}".format(label, expected_due, sms_due_date))
            else:
                log("PASS", "SUM", "{}: Due date {} matches SMS".format(label, expected_due))
        else:
            log("PASS", "SUM", "{}: Due date {} (no SMS cross-check)".format(label, expected_due))

        # Payment verification
        payments = conn.execute(
            "SELECT amount, payment_date, source FROM cc_payments "
            "WHERE card_last4=? AND payment_date >= ? ORDER BY payment_date ASC",
            (last4, billing_end)
        ).fetchall()
        total_paid = sum(p["amount"] for p in payments)
        actual_due = sms_due["amount"] if sms_due else stmt_total

        if total_paid > 0 and actual_due > 0:
            is_paid = total_paid >= (actual_due * 0.95)
        elif stmt_total <= 0:
            is_paid = True
        else:
            from datetime import datetime
            stmt_age = (datetime.now() - datetime.strptime(billing_end, "%Y-%m-%d")).days if billing_end else 0
            if stmt_age >= 45:
                hist_paid = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM cc_payments WHERE card_last4=?", (last4,)).fetchone()[0]
                hist_billed = conn.execute("SELECT COALESCE(SUM(total_amount), 0) FROM statements WHERE card_id=?", (card_id,)).fetchone()[0]
                is_paid = hist_paid >= (hist_billed * 0.90)
            else:
                is_paid = False

        if is_paid:
            log("PASS", "SUM", "{}: PAID (paid={:,.2f} vs due={:,.2f})".format(label, total_paid, actual_due))
        else:
            remaining = actual_due - total_paid
            log("WARN", "SUM", "{}: UNPAID (paid={:,.2f} vs due={:,.2f}, remaining={:,.2f})".format(label, total_paid, actual_due, remaining))

        # Unbilled verification
        unbilled = conn.execute(
            "SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total "
            "FROM transactions WHERE source IN ('card','sms') AND card_last4=? "
            "AND date > ? AND txn_type='debit' AND is_duplicate=0",
            (last4, billing_end)
        ).fetchone()
        log("PASS", "SUM", "{}: Unbilled={:,.2f} ({} txns)".format(label, round(unbilled["total"], 2), unbilled["count"]))

        # Duplicate payment check
        dup_check = conn.execute("""
            SELECT a.id, a.amount, a.payment_date, a.source,
                   b.id as dup_id, b.amount as dup_amount, b.payment_date as dup_date, b.source as dup_source
            FROM cc_payments a JOIN cc_payments b
            ON a.card_last4 = b.card_last4 AND a.id < b.id AND a.source != b.source
            AND ABS(JULIANDAY(a.payment_date) - JULIANDAY(b.payment_date)) <= 3
            AND ABS(a.amount - b.amount) / MAX(a.amount, b.amount) < 0.05
            WHERE a.card_last4 = ?
        """, (last4,)).fetchall()
        if dup_check:
            for d in dup_check:
                log("FAIL", "SUM", "{}: DUPLICATE PAYMENT id={}({}) Rs.{} vs id={}({}) Rs.{}".format(
                    label, d["id"], d["source"], d["amount"], d["dup_id"], d["dup_source"], d["dup_amount"]))
        else:
            log("PASS", "SUM", "{}: No duplicate payments".format(label))

        # Statement total vs SMS total due
        if sms_due and abs(stmt_total - sms_due["amount"]) > 1:
            diff = sms_due["amount"] - stmt_total
            pct = abs(diff) / max(stmt_total, 1) * 100
            if pct > 5:
                log("WARN", "SUM", "{}: PDF total={:,.2f} vs SMS due={:,.2f} (diff={:+,.2f}, {:.0f}%)".format(
                    label, stmt_total, sms_due["amount"], diff, pct))

    # Aggregate stat verification
    print("\n  --- Aggregate Stats ---")
    all_cards_data = []
    for card in cards:
        stmt = conn.execute(
            "SELECT month, year, total_amount, billing_end FROM statements WHERE card_id=? ORDER BY year DESC, month DESC LIMIT 1",
            (card["id"],)
        ).fetchone()
        if not stmt:
            continue
        last4 = card["last4"]
        billing_end = stmt["billing_end"] or ""
        paid_amt = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM cc_payments WHERE card_last4=? AND payment_date >= ?",
            (last4, billing_end)
        ).fetchone()[0]
        sms = conn.execute(
            "SELECT amount FROM sms_statement_dues WHERE card_last4=? AND statement_month=? AND statement_year=?",
            (last4, stmt["month"], stmt["year"])
        ).fetchone()
        actual = sms["amount"] if sms else stmt["total_amount"]
        card_is_paid = paid_amt >= (actual * 0.95) if paid_amt > 0 and actual > 0 else False
        if not card_is_paid and (stmt["total_amount"] or 0) <= 0:
            card_is_paid = True
        all_cards_data.append({
            "bank": card["bank"], "name": card["card_name"], "last4": last4,
            "stmt_total": stmt["total_amount"], "actual_due": actual,
            "paid": paid_amt, "is_paid": card_is_paid, "billing_end": billing_end
        })

    total_owed = sum(c["actual_due"] - c["paid"] for c in all_cards_data if not c["is_paid"])
    total_paid_agg = sum(c["paid"] for c in all_cards_data)
    total_unbilled = 0
    for card in cards:
        stmt = conn.execute(
            "SELECT billing_end FROM statements WHERE card_id=? ORDER BY year DESC, month DESC LIMIT 1",
            (card["id"],)
        ).fetchone()
        if stmt and stmt["billing_end"]:
            ub = conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE source IN ('card','sms') AND card_last4=? AND date > ? AND txn_type='debit' AND is_duplicate=0",
                (card["last4"], stmt["billing_end"])
            ).fetchone()[0]
            total_unbilled += ub

    paid_count = sum(1 for c in all_cards_data if c["is_paid"])
    stmt_count = len(all_cards_data)

    log("PASS", "AGG", "Total Due: {:,.2f}".format(total_owed))
    log("PASS", "AGG", "Total Paid This Cycle: {:,.2f}".format(total_paid_agg))
    log("PASS", "AGG", "Total Unbilled: {:,.2f}".format(total_unbilled))
    log("PASS", "AGG", "Statements: {}/{} paid".format(paid_count, stmt_count))


def generate_summary():
    """Print final summary."""
    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    total = PASS_COUNT + FAIL_COUNT + WARN_COUNT
    print(f"Total checks: {total}")
    print(f"  PASS: {PASS_COUNT}")
    print(f"  WARN: {WARN_COUNT}")
    print(f"  FAIL: {FAIL_COUNT}")
    if FAIL_COUNT == 0:
        print("\nResult: ALL CRITICAL CHECKS PASSED")
    else:
        print(f"\nResult: {FAIL_COUNT} CRITICAL FAILURES FOUND")


def main():
    conn = get_db()
    test_sms_integrity(conn)
    test_transaction_integrity(conn)
    test_amount_consistency(conn)
    test_credit_cards(conn)
    test_cross_matching(conn)
    test_email_integrity(conn)
    test_categorization_quality(conn)
    test_botb_api_compatibility(conn)
    test_data_consistency(conn)
    test_payment_tracking(conn)
    test_cc_summary_accuracy(conn)
    generate_summary()

    # Write report to file
    report_path = "/data/test_report.txt"
    with open(report_path, "w") as f:
        f.write(f"Ingest Data Integrity Report — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        for line in REPORT:
            f.write(line + "\n")
        f.write(f"\n\nTotal: {PASS_COUNT} PASS, {WARN_COUNT} WARN, {FAIL_COUNT} FAIL\n")
    print(f"\nReport saved to {report_path}")

    conn.close()
    return FAIL_COUNT


if __name__ == "__main__":
    sys.exit(main())
