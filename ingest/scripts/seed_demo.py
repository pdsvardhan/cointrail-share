"""Fill a fresh Ingest database with synthetic data so the whole stack can be tried with no personal data.

    INGEST_DB=/data/ingest.db CARDS_CONFIG_PATH=/data/cards.json python scripts/seed_demo.py [--months 6]

Everything here is invented (merchants are real brand names, amounts and dates are random but seeded, so
the same run always produces the same data). Safe to re-run: rows are keyed by source_ref.
"""
import argparse
import os
import random
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
from modules.categorizer import seed_aliases  # noqa: E402
from modules.txn_store import insert_transaction  # noqa: E402

# (description as it appears in a bank SMS, low, high, per-month count)
SPEND = [
    ("SWIGGY", 150, 900, 6), ("ZOMATO", 150, 800, 3), ("BLINKIT", 100, 700, 4),
    ("AMAZON PAY INDIA", 300, 6000, 3), ("UBER INDIA", 80, 600, 4), ("BIGBASKET", 300, 2500, 2),
    ("BOOKMYSHOW", 300, 1200, 1), ("DECATHLON", 500, 4000, 1), ("INDIAN OIL FUEL", 500, 3000, 2),
]
# fixed monthly subscriptions: (description, amount, day of month)
RECURRING = [("NETFLIX.COM", 649, 3), ("SPOTIFY", 119, 8), ("GOOGLE ONE", 130, 14)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=6)
    args = ap.parse_args()

    db.init_db()
    db.run_migrations()
    db.run_billing_migrations()
    db.run_c1_r4_migrations()
    db.run_iter18_migrations()
    db.run_iter203_be2_migrations()
    db.run_iter203_cards_migrations()
    seed_aliases()
    db.sync_cards_config()
    conn = db.get_db()
    cards = conn.execute("SELECT bank, last4 FROM cards WHERE active = 1 ORDER BY id").fetchall()
    if not cards:
        sys.exit("No cards found. Copy data.example/cards.example.json to $CARDS_CONFIG_PATH first.")

    rng = random.Random(42)
    today = date.today()
    start = today - timedelta(days=30 * args.months)
    added = 0

    def put(ref, d, amount, desc, card, txn_type="debit"):
        nonlocal added
        r = insert_transaction(conn, amount=amount, date=d.isoformat(), source="sms", source_ref=f"demo-{ref}",
                               raw_description=desc, bank=card["bank"], card_last4=card["last4"],
                               txn_type=txn_type, link=False)
        added += r["status"] == "inserted"

    n = 0
    month = date(start.year, start.month, 1)
    while month <= today:
        for desc, lo, hi, count in SPEND:
            for _ in range(count):
                d = month + timedelta(days=rng.randint(0, 27))
                if start <= d <= today:
                    n += 1
                    put(n, d, round(rng.uniform(lo, hi), 2), desc, rng.choice(cards))
        for desc, amount, day in RECURRING:
            d = month + timedelta(days=day - 1)
            if start <= d <= today:
                n += 1
                put(n, d, amount, desc, cards[0])
        d = month + timedelta(days=24)
        if start <= d <= today:  # a card bill payment each month
            n += 1
            put(n, d, round(rng.uniform(8000, 25000), 2), "CREDIT CARD BILL PAYMENT", cards[0], "credit")
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)

    print(f"Seeded {added} demo transactions across {len(cards)} cards ({args.months} months).")


if __name__ == "__main__":
    main()
