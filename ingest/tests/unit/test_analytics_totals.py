"""Honest totals — iter-18 H5b / M13, contract 4 of the iter-18 brief.

One fixture month (October 2026, "today" = 20 Oct IST) with every kind of row the definition has
to handle, written through txn_store.insert_transaction exactly as the pipeline writes them. Every
number in the overview, categories and trends responses is asserted, then asserted again with
every kind wiped to NULL (rows from before the backfill, classified on the fly) and again after
backfill_kinds() has written them back."""
from datetime import date

import pytest

from modules.kinds_backfill import backfill_kinds
from modules.txn_store import insert_transaction
from routes import analytics

TODAY = date(2026, 10, 20)


@pytest.fixture
def month(conn, make_card, monkeypatch):
    monkeypatch.setattr(analytics, "today_ist", lambda: TODAY)
    make_card("HDFC", "9434", name="Swiggy")
    make_card("ICICI", "0026", name="Amazon Pay")
    make_card("HDFC", "6611", name="Account", active=0)
    make_card("HSBC", "4706", name="Account", active=0)

    def ins(desc, amount, d, source, card, ctx, txn_type="debit", **kw):
        return insert_transaction(conn, amount=amount, date=d, source=source, raw_description=desc,
                                  source_ref=f"{source}-{desc}-{d}-{amount}", card_last4=card,
                                  txn_type=txn_type, context=ctx, **kw)

    # ---- October 2026 ------------------------------------------------------------------------
    ins("SWIGGY", 1200, "2026-10-03", "sms", "9434", "cc",
        raw_text="Spent Rs.1200 On HDFC Bank Card 9434 At SWIGGY On 2026-10-03:20:15:00.Not You? To Block+")
    # a purchase seen twice: the card SMS, then the statement line (merged: counts once)
    sms_copy = ins("WESTSIDE", 2000, "2026-10-05", "sms", "0026", "cc")
    ins("Westside VIJAYAWADA IN 99", 2000, "2026-10-05", "card", "0026", "card")
    ins("TRENT LIMITED", 2299, "2026-10-18", "sms", "9434", "cc")
    ins("SWIGGY FOOD BANGALORE", 300, "2026-10-06", "sms", "9434", "cc", txn_type="credit",
        raw_text="Alert! Rs. 300 refunded by SWIGGY FOOD            BANGALORE     IND on 06/OCT/2026 & adjusted "
                 "against HDFC Bank Credit Card 9434 View updated balance here: https://1.hdfc.bank.in/HDFCBK/s/mqzzz017")
    ins("10% Swiggy CashBack", 120, "2026-10-07", "card", "9434", "card", txn_type="credit")
    ins("Principal Amount Amortization - <2/6>AMAZON PAY IN", 4540, "2026-10-10", "card", "0026", "card")
    ins("Interest Amount Amortization - <2/6>AMAZON PAY IND", 311, "2026-10-10", "card", "0026", "card")
    ins("LATE PAYMENT FEE", 500, "2026-10-12", "card", "9434", "card")
    ins("RAMESH KUMAR", 2000, "2026-10-08", "sms", "6611", "upi",
        raw_text="Sent Rs.2000.00 From HDFC Bank A/C *6611 To RAMESH KUMAR On 08/10/26 Ref 600000000017 "
                 "Not You? Call 18002586161/SMS BLOCK UPI to 7308080808")
    ins("relative@axl (UPI 600000000014)", 10000, "2026-10-09", "sms", "6611", "upi",
        merchant="Relative", category="Family Remittance")
    ins("ZERODHA BROKING LTD", 5000, "2026-10-11", "sms", "6611", "upi")
    ins("CRED Club", 15000, "2026-10-07", "sms", "4706", "upi",
        raw_text="INR 15000.00 is paid from HSBC account XXXXXX4706 to CRED Club on 07-Oct-26 with ref 600000000022.")
    ins("OWN ACCOUNT TRANSFER", 50000, "2026-10-02", "sms", "6611", "bank",
        merchant="Self Transfer", category="Self Transfer")
    ins("CREDIT CARD PAYMENT", 4599, "2026-10-07", "card", "9434", "card", txn_type="credit")
    ins("HSBC credit", 85000, "2026-10-01", "sms", "4706", "bank", txn_type="credit",
        raw_text="Your HSBC A/c XXXXXX4706 has been credited with INR 85,000.00 on 01OCT.")
    ins("HDFC ATM", 1900, "2026-10-15", "sms", "6611", "bank")
    # ---- September 2026 -------------------------------------------------------------------------
    ins("SWIGGY", 1000, "2026-09-05", "sms", "9434", "cc")
    ins("RAMA FABRICS VIJAYAWADA IN 16", 3000, "2026-09-25", "card", "0026", "card")
    ins("RAMESH KUMAR", 700, "2026-09-06", "sms", "6611", "upi")
    ins("IRCTC Train TicketingCentral Delh", 400, "2026-09-26", "card", "0026", "card", txn_type="credit")
    # ---- August 2026 ----------------------------------------------------------------------------
    ins("SWIGGY", 500, "2026-08-31", "sms", "9434", "cc")

    assert conn.execute("SELECT is_duplicate FROM transactions WHERE id=?", (sms_copy["id"],)).fetchone()[0] == 1
    kinds = dict(conn.execute("SELECT raw_description, kind FROM transactions WHERE date >= '2026-10-01'").fetchall())
    assert kinds["SWIGGY FOOD BANGALORE"] == "refund" and kinds["10% Swiggy CashBack"] == "cashback"
    assert kinds["Principal Amount Amortization - <2/6>AMAZON PAY IN"] == "emi_principal"
    assert kinds["CRED Club"] == kinds["CREDIT CARD PAYMENT"] == "bill_payment"
    return conn


OCT_CATEGORIES = [
    {"category": "Shopping", "total": 4299.0, "count": 2, "gross": 4299.0, "refunds": 0.0},
    {"category": "ATM Withdrawal", "total": 1900.0, "count": 1, "gross": 1900.0, "refunds": 0.0},
    {"category": "Food & Dining", "total": 780.0, "count": 1, "gross": 1200.0, "refunds": 420.0},
    {"category": "Bank Charges", "total": 500.0, "count": 1, "gross": 500.0, "refunds": 0.0},
    {"category": "EMI", "total": 311.0, "count": 1, "gross": 311.0, "refunds": 0.0},
]
OCT_DAILY = {"2026-10-03": (1200.0, 1), "2026-10-05": (2000.0, 1), "2026-10-06": (-300.0, 0),
             "2026-10-07": (-120.0, 0), "2026-10-10": (311.0, 1), "2026-10-12": (500.0, 1),
             "2026-10-15": (1900.0, 1), "2026-10-18": (2299.0, 1)}
ALL_DAILY = {"2026-08-31": (500.0, 1), "2026-09-05": (1000.0, 1), "2026-09-25": (3000.0, 1),
             "2026-09-26": (-400.0, 0), **OCT_DAILY}


def check_overview(conn):
    o = analytics.analytics_overview("2026-10")
    top = o.pop("top_transactions")
    assert o == {
        "period": "2026-10",
        "range": None,                   # iteration 203 E1: set when from/to are sent instead
        "comparison_basis": "same_days_last_month",
        "data_since": "2026-08-31",      # the span of the data (E1 "All", E2 M2)
        "data_until": "2026-10-18",
        "total": 7790.0,                 # 8210 spent - 420 refunded/cashback
        "gross_spend": 8210.0,
        "refunds": 420.0,
        "fees_interest": 811.0,          # late fee 500 + EMI interest 311
        "outflows": {"sent_to_people": {"total": 12000.0, "count": 2},
                     "investments": {"total": 5000.0, "count": 1},
                     "loans": {"total": 0.0, "count": 0}},
        "txn_count": 6,
        "days_elapsed": 20,
        "daily_avg": 389.5,
        "avg_txn": 1368.33,
        "prev_month_total": 3600.0,      # Sep: 1000 + 3000 - 400 IRCTC refund
        "prev_month_txn_count": 2,
        "prev_same_period_total": 1000.0,  # Sep 1-20 only
        "categories": OCT_CATEGORIES,
        "daily_spend": [{"date": d, "total": t, "count": n} for d, (t, n) in OCT_DAILY.items()],
        "highest_day": {"date": "2026-10-18", "total": 2299.0, "count": 1},
        "top_category": OCT_CATEGORIES[0],
    }
    assert [(t["merchant"], t["amount"], t["date"], t["category"]) for t in top] == [
        ("Trent (Westside/Zudio)", 2299.0, "2026-10-18", "Shopping"),
        ("Westside", 2000.0, "2026-10-05", "Shopping"),
        ("ATM Withdrawal", 1900.0, "2026-10-15", "ATM Withdrawal"),
        ("Swiggy", 1200.0, "2026-10-03", "Food & Dining"),
        ("Late Payment Fee", 500.0, "2026-10-12", "Bank Charges"),
        ("Interest Amortization", 311.0, "2026-10-10", "EMI"),
    ]
    assert {t["source"] for t in top} == {"sms", "card"} and all(t["id"] and t["raw_description"] for t in top)


def check_categories(conn):
    c = analytics.analytics_categories("2026-10")
    merchants = {x["category"]: x.pop("merchants") for x in c["categories"]}
    assert c == {"total": 7790.0, "categories": OCT_CATEGORIES}
    assert merchants == {
        "Shopping": [{"name": "Trent (Westside/Zudio)", "total": 2299.0, "count": 1},
                     {"name": "Westside", "total": 2000.0, "count": 1}],
        "ATM Withdrawal": [{"name": "ATM Withdrawal", "total": 1900.0, "count": 1}],
        "Food & Dining": [{"name": "Swiggy", "total": 780.0, "count": 1}],
        "Bank Charges": [{"name": "Late Payment Fee", "total": 500.0, "count": 1}],
        "EMI": [{"name": "Interest Amortization", "total": 311.0, "count": 1}],
    }

    food = analytics.analytics_categories("2026-10", category="Food & Dining")
    recent = food.pop("recent_transactions")      # iteration 203 E2 M4: signed, newest first
    assert [(r["date"], r["signed_amount"], r["kind"]) for r in recent] == [
        ("2026-10-07", -120.0, "cashback"), ("2026-10-06", -300.0, "refund"), ("2026-10-03", 1200.0, "purchase")]
    assert food == {
        "category": "Food & Dining", "group": "spend", "total": 780.0, "gross": 1200.0, "refunds": 420.0,
        "merchants": [{"name": "Swiggy", "total": 780.0, "count": 1, "refunds": 420.0, "transactions": [
            {"amount": 120.0, "date": "2026-10-07", "description": "10% Swiggy CashBack",
             "txn_type": "credit", "kind": "cashback"},
            {"amount": 300.0, "date": "2026-10-06", "description": "SWIGGY FOOD BANGALORE",
             "txn_type": "credit", "kind": "refund"},
            {"amount": 1200.0, "date": "2026-10-03", "description": "SWIGGY", "txn_type": "debit",
             "kind": "purchase"},
        ]}],
    }
    emi = analytics.analytics_categories("2026-10", category="EMI")
    assert emi["total"] == 311.0 and [m["name"] for m in emi["merchants"]] == ["Interest Amortization"]
    people = analytics.analytics_categories("2026-10", category="Transfer")
    assert people["group"] == "sent_to_people" and people["total"] == 2000.0


def check_trends(conn):
    t = analytics.analytics_trends(12)
    assert t["monthly"] == [
        {"month": "2026-08", "total": 500.0, "count": 1, "gross_spend": 500.0, "refunds": 0.0,
         "sent_to_people": 0.0, "investments": 0.0, "loans": 0.0},
        {"month": "2026-09", "total": 3600.0, "count": 2, "gross_spend": 4000.0, "refunds": 400.0,
         "sent_to_people": 700.0, "investments": 0.0, "loans": 0.0},
        {"month": "2026-10", "total": 7790.0, "count": 6, "gross_spend": 8210.0, "refunds": 420.0,
         "sent_to_people": 12000.0, "investments": 5000.0, "loans": 0.0},
    ]
    assert t["category_trends"] == {
        "Food & Dining": {"2026-08": 500.0, "2026-09": 1000.0, "2026-10": 780.0},
        "Shopping": {"2026-09": 3000.0, "2026-10": 4299.0},
        "Travel": {"2026-09": -400.0},
        "EMI": {"2026-10": 311.0},
        "Bank Charges": {"2026-10": 500.0},
        "ATM Withdrawal": {"2026-10": 1900.0},
    }
    assert t["categories"] == ["ATM Withdrawal", "Bank Charges", "EMI", "Food & Dining", "Shopping", "Travel"]
    expected_dow = {n: [0.0, 0] for n in range(7)}
    for d, (total, _) in ALL_DAILY.items():
        expected_dow[date.fromisoformat(d).weekday()][0] += total
        expected_dow[date.fromisoformat(d).weekday()][1] += 1
    assert t["weekday_distribution"] == [
        {"dow": n, "avg_spend": round(v[0] / max(1, v[1]), 2), "total_spend": round(v[0], 2), "day_count": v[1]}
        for n, v in expected_dow.items()
    ]
    assert t["top_merchants"] == [
        {"name": "RAMA FABRICS VIJAYAWADA IN 16", "total": 3000.0, "count": 1},
        {"name": "Trent (Westside/Zudio)", "total": 2299.0, "count": 1},
        {"name": "Swiggy", "total": 2280.0, "count": 3},
        {"name": "Westside", "total": 2000.0, "count": 1},
        {"name": "ATM Withdrawal", "total": 1900.0, "count": 1},
        {"name": "Late Payment Fee", "total": 500.0, "count": 1},
        {"name": "Interest Amortization", "total": 311.0, "count": 1},
        {"name": "IRCTC", "total": -400.0, "count": 0},
    ]


def check_heatmap_and_days(conn):
    assert analytics.analytics_heatmap(6) == {
        "heatmap": [{"date": d, "total": t, "count": n} for d, (t, n) in ALL_DAILY.items()]}
    day = analytics.analytics_day_transactions("2026-10-10")
    assert day["total"] == 311.0 and day["count"] == 1                  # the principal line is not spend
    assert day["transactions"][0]["kind"] == "interest"
    refund_day = analytics.analytics_day_transactions("2026-10-06")
    assert refund_day["total"] == -300.0 and refund_day["count"] == 1
    assert refund_day["transactions"][0]["signed_amount"] == -300.0
    assert refund_day["transactions"][0]["txn_type"] == "credit"
    assert analytics.analytics_day_transactions("2026-10-07")["count"] == 1   # cashback; CRED & card payment excluded


def check_all(conn):
    check_overview(conn)
    check_categories(conn)
    check_trends(conn)
    check_heatmap_and_days(conn)


def test_totals_with_stored_kinds(month):
    check_all(month)


def test_totals_identical_before_and_after_the_kind_backfill(month):
    month.execute("UPDATE transactions SET kind = NULL")
    month.commit()
    check_all(month)                       # classified on the fly
    assert backfill_kinds(month, dry_run=False)["updated"] == 22
    check_all(month)                       # read from the column


def test_complete_and_future_months(month):
    sep = analytics.analytics_overview("2026-09")
    assert sep["days_elapsed"] == 30 and sep["daily_avg"] == 120.0
    assert sep["total"] == 3600.0 and sep["refunds"] == 400.0
    # a finished month is compared with the whole previous month (Aug 31 included)
    assert sep["prev_month_total"] == 500.0 and sep["prev_same_period_total"] == 500.0
    nov = analytics.analytics_overview("2026-11")
    assert nov["days_elapsed"] == 0 and nov["daily_avg"] == 0.0 and nov["total"] == 0.0
    assert nov["prev_month_total"] == 7790.0 and nov["prev_same_period_total"] == 0.0
    assert analytics.analytics_overview()["period"] == "2026-10"          # default: the IST month


def test_days_elapsed_in_ist():
    assert analytics.days_elapsed(2026, 10, date(2026, 10, 1)) == 1
    assert analytics.days_elapsed(2026, 10, date(2026, 10, 31)) == 31
    assert analytics.days_elapsed(2026, 2, date(2026, 10, 20)) == 28
    assert analytics.days_elapsed(2026, 12, date(2026, 10, 20)) == 0


def test_groups_are_module_constants():
    assert analytics.category_group("Finance") == analytics.category_group("Self Transfer") == "excluded"
    assert analytics.category_group("Family Remittance") == "sent_to_people"
    assert analytics.category_group("Home Construction") == "investments"
    assert analytics.category_group("Housemate Expenses") == analytics.category_group(None) == "spend"
