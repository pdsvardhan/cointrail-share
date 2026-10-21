"""/cards/{id}/transactions behind the card detail page — iteration 203 E2 (B3, M7, M8, M9).

B3: the sidebar's Category Breakdown / Top Merchants added every row with abs(amount): a Rs 50k
bill payment became a category and the #1 merchant. M8: aggregates were computed over the
LIMITed rows. M7: "Current Statement" ignored the summary's 3-day tail rule, so the list did not
match the Unbilled tile. M9: an add-on card's spends were reachable nowhere.
"""
from datetime import date

import pytest

import routes.cards as rc
import services.cards_service as cs
from modules.txn_store import insert_transaction

TODAY = date(2026, 10, 25)


def line(conn, last4, on, amount, desc, kind="debit", source="card", raw_text=None):
    return insert_transaction(conn, amount=amount, date=on, source=source, raw_description=desc,
                              source_ref=f"{source}-{last4}-{on}-{amount}-{desc}", bank="HDFC", card_last4=last4,
                              txn_type=kind, raw_text=raw_text, context="card" if source == "card" else "cc",
                              link=False)["id"]


def statement(conn, last4, start, end, stated):
    card = conn.execute("SELECT id FROM cards WHERE last4=?", (last4,)).fetchone()["id"]
    conn.execute(
        "INSERT INTO statements (card_id, month, year, total_amount, stated_total, billing_start, billing_end, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'parsed')", (card, int(end[5:7]), int(end[:4]), stated, stated, start, end))
    conn.commit()


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr(rc, "today_ist", lambda: TODAY)
    return TODAY


@pytest.fixture
def swiggy_card(conn, make_card, today):
    card = make_card("HDFC", "4434", name="Swiggy")
    line(conn, "4434", "2026-10-03", 1200, "SWIGGY", source="sms")
    line(conn, "4434", "2026-10-06", 300, "SWIGGY FOOD BANGALORE", kind="credit", source="sms",
         raw_text="Alert! Rs. 300 refunded by SWIGGY FOOD BANGALORE IND on 06/OCT/2026 & adjusted against "
                  "HDFC Bank Credit Card 4434")
    line(conn, "4434", "2026-10-07", 50000, "CREDIT CARD PAYMENT", kind="credit")
    line(conn, "4434", "2026-10-07", 120, "10% Swiggy CashBack", kind="credit")
    line(conn, "4434", "2026-10-10", 4540, "Principal Amount Amortization - <2/6>AMAZON PAY IN")
    line(conn, "4434", "2026-10-10", 311, "Interest Amount Amortization - <2/6>AMAZON PAY IND")
    return card


def test_card_totals_count_spending_only(swiggy_card):
    """B3: the bill payment and the EMI principal are listed but never added up; refunds and
    cashback net inside their own category."""
    d = rc.card_transactions(swiggy_card, date_from="2026-10-01", date_to="2026-10-25")
    assert d["count"] == d["total_count"] == 6
    assert d["categories"] == {"Food & Dining": 780.0, "EMI": 311.0}
    assert d["total"] == 1091.0 and d["refunds"] == {"total": 420.0, "count": 2}
    assert [(m["name"], m["total"], m["count"]) for m in d["top_merchants"]] == [
        ("Swiggy", 780.0, 1), ("Interest Amortization", 311.0, 1)]
    food = next(c for c in d["category_breakdown"] if c["category"] == "Food & Dining")
    # the merchants inside a category add up to the category
    assert round(sum(m["total"] for m in food["merchants"]), 2) == food["total"] == 780.0
    assert "Finance" not in d["categories"]


def test_aggregates_cover_every_row_even_when_the_list_is_capped(swiggy_card):
    """M8: 'All Time' on a card with more than 500 rows totalled only the newest 500."""
    full = rc.card_transactions(swiggy_card, date_from="2026-10-01", date_to="2026-10-25")
    capped = rc.card_transactions(swiggy_card, date_from="2026-10-01", date_to="2026-10-25", limit=2)
    assert len(capped["transactions"]) == capped["count"] == 2 and capped["total_count"] == 6
    assert [t["date"] for t in capped["transactions"]] == ["2026-10-10", "2026-10-10"]
    for key in ("total", "categories", "category_breakdown", "top_merchants", "refunds"):
        assert capped[key] == full[key]


def test_unbilled_list_matches_the_unbilled_tile(conn, make_card, today):
    """M7: the list behind 'Current Statement' is exactly the summary's unbilled set, including a
    spend in the last days of the cycle that the statement did not bill."""
    card = make_card("SBI", "7711", statement_day=2, due_day=20)
    statement(conn, "7711", "2026-09-02", "2026-10-01", 500.0)
    line(conn, "7711", "2026-09-28", 500, "AMAZON")                    # a statement line: billed
    line(conn, "7711", "2026-09-20", 100, "ZOMATO", source="sms")      # early in the cycle: billed
    line(conn, "7711", "2026-09-30", 250, "BLINKIT", source="sms")     # tail, matched no line: unbilled
    line(conn, "7711", "2026-10-05", 700, "SWIGGY", source="sms")
    line(conn, "7711", "2026-10-10", 50, "SWIGGY FOOD reversal/cashback", kind="credit", source="sms")
    d = rc.card_transactions(card, period="unbilled")
    tile = {c["last4"]: c for c in cs.build_cards_summary(conn, today=TODAY)["cards"]}["7711"]["unbilled"]
    debits = [t for t in d["transactions"] if t["txn_type"] == "debit"]
    assert sorted(t["amount"] for t in debits) == [250.0, 700.0]
    assert (round(sum(t["amount"] for t in debits), 2), len(debits)) == (tile["total"], tile["count"]) == (950.0, 2)
    assert d["unbilled_since"] == "2026-10-01"


def test_billing_group_detail_shows_the_add_on_card(conn, make_card, today):
    """M9: the merged ICICI tile linked to the primary, whose page showed only its own last-4."""
    primary = make_card("ICICI", "1919", statement_day=21, due_day=10)
    addon = make_card("ICICI", "8813", statement_day=21, due_day=10)
    conn.execute("UPDATE cards SET billing_group='g' WHERE last4 IN ('1919', '8813')")
    conn.commit()
    statement(conn, "1919", "2026-09-21", "2026-10-20", 900.0)
    line(conn, "1919", "2026-10-22", 400, "AMAZON", source="sms")
    line(conn, "8813", "2026-10-23", 600, "MAKEMYTRIP", source="sms")
    for card in (primary, addon):
        d = rc.card_transactions(card, period="unbilled")
        assert d["card_last4s"] == ["1919", "8813"]
        assert sorted(t["amount"] for t in d["transactions"]) == [400.0, 600.0]
        assert d["unbilled_since"] == "2026-10-20"


def test_unknown_card(conn, today):
    assert rc.card_transactions(999)["transactions"] == []
