"""Expenses date ranges and the drill-downs behind them — iteration 203 E1 / E2 (B1, B4, M1, M4, C2).

The Expenses page's 3M / 6M / 1Y / All pills used to hide the month picker and nothing else: every
endpoint accepted only `period=YYYY-MM`. These tests pin the range contract (`from` / `to`, IST
calendar dates, inclusive) on overview, categories, trends, heatmap, the period's full transaction
list and the merchant drill, all on the same spend definition (`classify`). "Today" is 20 Oct 2026.
"""
from datetime import date

import pytest
from fastapi import HTTPException

from modules.txn_store import insert_transaction
from routes import analytics

TODAY = date(2026, 10, 20)
FROM, TO = "2026-08-01", "2026-10-20"        # what the 3M pill sends on 20 Oct


@pytest.fixture
def months(conn, make_card, monkeypatch):
    monkeypatch.setattr(analytics, "today_ist", lambda: TODAY)
    make_card("HDFC", "4434", name="Swiggy")
    make_card("ICICI", "0066", name="Amazon Pay")
    make_card("HDFC", "6611", name="Account", active=0)

    def ins(desc, amount, d, card, ctx, txn_type="debit", **kw):
        return insert_transaction(conn, amount=amount, date=d, source="sms", raw_description=desc,
                                  source_ref=f"sms-{desc}-{d}-{amount}", card_last4=card,
                                  txn_type=txn_type, context=ctx, **kw)["id"]

    ins("SWIGGY", 700, "2026-05-20", "4434", "cc")                 # in the window before 3M
    ins("SWIGGY", 400, "2026-07-15", "4434", "cc")
    ins("SWIGGY", 500, "2026-08-10", "4434", "cc")
    ins("WESTSIDE", 1000, "2026-08-20", "0066", "cc")
    ins("SWIGGY", 1000, "2026-09-05", "4434", "cc")
    ins("SWIGGY FOOD BANGALORE", 300, "2026-09-06", "4434", "cc", txn_type="credit",
        raw_text="Alert! Rs. 300 refunded by SWIGGY FOOD BANGALORE IND on 06/SEP/2026 & adjusted "
                 "against HDFC Bank Credit Card 4434")
    ins("RAMESH KUMAR", 2000, "2026-09-08", "6611", "upi",
        raw_text="Sent Rs.2000.00 From HDFC Bank A/C *6611 To RAMESH KUMAR On 08/09/26 Ref 600000000099")
    ins("SWIGGY", 1200, "2026-10-03", "4434", "cc")
    ins("HDFC ATM", 1900, "2026-10-15", "6611", "bank")
    ins("TRENT LIMITED", 2299, "2026-10-18", "4434", "cc")
    ins("SWIGGY INSTAMART", 450, "2026-10-04", "4434", "cc")      # a different merchant that contains "Swiggy"
    return conn


def _merchant_of(conn, desc):
    return conn.execute("SELECT merchant, category FROM transactions WHERE raw_description=?", (desc,)).fetchone()


# --- overview --------------------------------------------------------------------------------------------------

def test_overview_over_a_range_equals_the_sum_of_its_months(months):
    rng = analytics.analytics_overview(date_from=FROM, date_to=TO)
    by_month = [analytics.analytics_overview(p)["total"] for p in ("2026-08", "2026-09", "2026-10")]
    assert rng["total"] == round(sum(by_month), 2) == 8049.0      # 1500 + 700 + 5849
    assert rng["total"] != analytics.analytics_overview("2026-10")["total"]
    assert rng["range"] == {"from": FROM, "to": TO}
    assert rng["period"] is None
    assert (rng["data_since"], rng["data_until"]) == ("2026-05-20", "2026-10-18")
    assert rng["days_elapsed"] == 81                               # 31 + 30 + 20 calendar days
    assert rng["daily_avg"] == round(8049.0 / 81, 2)
    assert rng["outflows"]["sent_to_people"] == {"total": 2000.0, "count": 1}
    assert {d["date"][:7] for d in rng["daily_spend"]} == {"2026-08", "2026-09", "2026-10"}


def test_range_is_compared_with_the_preceding_window_of_equal_length(months):
    rng = analytics.analytics_overview(date_from=FROM, date_to=TO)
    # 81 days before 1 Aug: 12 May .. 31 Jul -> the 20 May and 15 Jul orders
    assert rng["comparison_basis"] == "previous_window"
    assert rng["comparison_window"] == {"from": "2026-05-12", "to": "2026-07-31", "days": 81}
    assert rng["prev_same_period_total"] == rng["prev_month_total"] == 1100.0


def test_a_range_ending_in_the_future_counts_only_the_days_so_far(months):
    rng = analytics.analytics_overview(date_from=FROM, date_to="2026-12-31")
    assert rng["days_elapsed"] == 81 and rng["total"] == 8049.0
    assert rng["comparison_window"]["days"] == 81


def test_month_mode_keeps_its_comparison_basis(months):
    assert analytics.analytics_overview("2026-10")["comparison_basis"] == "same_days_last_month"
    assert analytics.analytics_overview("2026-09")["comparison_basis"] == "previous_month"


@pytest.mark.parametrize("kw", [
    {"date_from": "2026-08-01"},                                   # half a range
    {"date_to": "2026-10-20"},
    {"date_from": "2026-10-21", "date_to": "2026-10-20"},          # backwards
    {"date_from": "2026-8-1", "date_to": "2026-10-20"},            # not YYYY-MM-DD
    {"date_from": "2026-02-30", "date_to": "2026-10-20"},          # no such day
])
def test_bad_ranges_are_rejected(months, kw):
    with pytest.raises(HTTPException) as e:
        analytics.analytics_overview(**kw)
    assert e.value.status_code == 400


# --- categories + the drill ------------------------------------------------------------------------------------

def test_categories_over_a_range(months):
    c = analytics.analytics_categories(date_from=FROM, date_to=TO)
    assert c["total"] == 8049.0
    food = next(x for x in c["categories"] if x["category"] == "Food & Dining")
    assert food["total"] == 2400.0 and food["refunds"] == 300.0     # 500 + 1000 - 300 + 1200


def test_category_drill_returns_recent_transactions_signed_and_newest_first(months):
    d = analytics.analytics_categories(date_from=FROM, date_to=TO, category="Food & Dining")
    recent = d["recent_transactions"]
    assert [r["date"] for r in recent] == sorted((r["date"] for r in recent), reverse=True)
    assert [(r["date"], r["signed_amount"]) for r in recent] == [
        ("2026-10-03", 1200.0), ("2026-09-06", -300.0), ("2026-09-05", 1000.0), ("2026-08-10", 500.0)]
    refund = recent[1]
    assert refund["txn_type"] == "credit" and refund["amount"] == 300.0 and refund["kind"] == "refund"
    assert all(r["id"] and r["merchant"] for r in recent)


def test_recent_transactions_are_not_capped_per_merchant(conn, make_card, monkeypatch):
    """M4: the list used to be built from at most 5 rows per merchant, so a merchant with 8 recent
    orders showed 5 and older rows from other merchants filled the gap."""
    monkeypatch.setattr(analytics, "today_ist", lambda: TODAY)
    make_card("HDFC", "4434", name="Swiggy")
    for day in range(1, 9):
        insert_transaction(conn, amount=100 + day, date=f"2026-10-{day:02d}", source="sms", raw_description="SWIGGY",
                           source_ref=f"s{day}", card_last4="4434", context="cc")
    insert_transaction(conn, amount=999, date="2026-09-01", source="sms", raw_description="ZOMATO",
                       source_ref="z", card_last4="4434", context="cc")
    d = analytics.analytics_categories(date_from="2026-09-01", date_to="2026-10-20", category="Food & Dining")
    assert [r["date"] for r in d["recent_transactions"][:8]] == [f"2026-10-{day:02d}" for day in range(8, 0, -1)]


# --- trends + heatmap --------------------------------------------------------------------------------------------

def test_trends_and_heatmap_follow_the_range(months):
    t = analytics.analytics_trends(date_from=FROM, date_to=TO)
    assert [m["month"] for m in t["monthly"]] == ["2026-08", "2026-09", "2026-10"]
    h = analytics.analytics_heatmap(date_from=FROM, date_to=TO)["heatmap"]
    assert min(d["date"] for d in h) >= FROM and max(d["date"] for d in h) <= TO
    assert "2026-07-15" not in {d["date"] for d in h}


def test_month_windows_are_calendar_months():
    """C2 / B4: '6 months' started on the 1st of (today - 180 days), up to 7 calendar months."""
    assert analytics._window_start(TODAY, 1) == "2026-10-01"
    assert analytics._window_start(TODAY, 6) == "2026-05-01"
    assert analytics._window_start(TODAY, 12) == "2025-11-01"
    assert analytics._window_start(date(2026, 1, 31), 3) == "2025-11-01"


# --- View All: every transaction of the period ----------------------------------------------------------------

def test_view_all_lists_every_counted_transaction_paged(months):
    """M1: 'View All' opened the same top-10 list. The period list is paged and uses the same spend
    definition as the headline (refunds signed, transfers to people and own money excluded)."""
    first = analytics.analytics_transactions(date_from=FROM, date_to=TO, sort="amount", offset=0, limit=3)
    assert first["total_count"] == 8                 # 7 spend debits + 1 refund
    assert [t["signed_amount"] for t in first["transactions"]] == [2299.0, 1900.0, 1200.0]
    rest = analytics.analytics_transactions(date_from=FROM, date_to=TO, sort="amount", offset=3, limit=50)
    assert len(rest["transactions"]) == 5 and rest["transactions"][-1]["signed_amount"] == -300.0
    everything = first["transactions"] + rest["transactions"]
    assert round(sum(t["signed_amount"] for t in everything), 2) == \
        analytics.analytics_overview(date_from=FROM, date_to=TO)["total"]
    assert "RAMESH KUMAR" not in {t["raw_description"] for t in everything}
    by_date = analytics.analytics_transactions(period="2026-10", sort="date", offset=0, limit=50)
    assert [t["date"] for t in by_date["transactions"]] == ["2026-10-18", "2026-10-15", "2026-10-04", "2026-10-03"]


# --- merchant drill --------------------------------------------------------------------------------------------

def test_merchant_drill_matches_the_clicked_merchant_exactly(months):
    """B4: 'Swiggy' used to pull in Swiggy Instamart (LIKE %name%), count bill payments and EMI
    principal, never net refunds, and return nothing for a row keyed by its raw description."""
    swiggy = _merchant_of(months, "SWIGGY")["merchant"]
    instamart = _merchant_of(months, "SWIGGY INSTAMART")["merchant"]
    assert swiggy != instamart
    d = analytics.analytics_merchant_detail(merchant=swiggy, category="Food & Dining", date_from=FROM, date_to=TO)
    assert d["total"] == 2400.0 and d["count"] == 3 and d["refunds"] == 300.0
    assert d["avg_transaction"] == round(2700.0 / 3, 2)
    assert instamart not in {t["merchant"] for t in d["transactions"]}
    assert [m["month"] for m in d["monthly"]] == ["2026-08", "2026-09", "2026-10"]
    assert d["range"] == {"from": FROM, "to": TO}
    # equals the merchant's figure on the category card for the same window
    cat = analytics.analytics_categories(date_from=FROM, date_to=TO, category="Food & Dining")
    assert next(m for m in cat["merchants"] if m["name"] == swiggy)["total"] == d["total"]


def test_merchant_drill_for_a_row_keyed_by_its_description(months):
    months.execute("UPDATE transactions SET merchant=NULL WHERE raw_description='WESTSIDE'")
    months.commit()
    cat = _merchant_of(months, "WESTSIDE")["category"]
    keys = {m["name"] for m in analytics.analytics_categories(date_from=FROM, date_to=TO, category=cat)["merchants"]}
    assert "WESTSIDE" in keys
    d = analytics.analytics_merchant_detail(merchant="WESTSIDE", category=cat, date_from=FROM, date_to=TO)
    assert d["count"] == 1 and d["total"] == 1000.0
