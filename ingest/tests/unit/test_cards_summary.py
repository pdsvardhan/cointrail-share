"""iter-18 H1 / H2 / M11: what /cards/summary says about each card's bill."""
from datetime import date

import pytest

import routes.cards as rc
import services.cards_service as cs
from modules.txn_store import insert_transaction

TODAY = date(2026, 10, 19)          # the day the owner compared Cointrail with CRED


def line(conn, last4, on, amount, desc, kind="debit", source="card", ref=None, dup=False):
    tid = insert_transaction(conn, amount=amount, date=on, source=source, source_ref=ref or f"{source}-{last4}-{on}-{amount}-{desc}",
                             raw_description=desc, bank="HDFC", card_last4=last4, txn_type=kind,
                             context="card" if source == "card" else "cc", link=False)["id"]
    if dup:
        conn.execute("UPDATE transactions SET is_duplicate=1 WHERE id=?", (tid,))
    return tid


def add_statement(conn, last4, billing_start, billing_end, stated=None, computed=None, total=None, month=None, year=None,
                  due_date=None, due_src=None):
    card = conn.execute("SELECT id FROM cards WHERE last4=?", (last4,)).fetchone()["id"]
    conn.execute(
        "INSERT INTO statements (card_id, month, year, total_amount, stated_total, computed_total, billing_start, billing_end, "
        "due_date, due_date_source, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'parsed')",
        (card, month or int(billing_end[5:7]), year or int(billing_end[:4]),
         total if total is not None else (stated if stated is not None else computed),
         stated, computed, billing_start, billing_end, due_date, due_src))


def summary(conn, today=TODAY):
    return {c["last4"]: c for c in cs.build_cards_summary(conn, today=today)["cards"]}


# --- M11: the status rules -------------------------------------------------------------------------------

@pytest.mark.parametrize("amount_due, paid, due, close, today, expect", [
    (13836.54, 13836.0, "2026-10-21", "2026-10-01", TODAY, ("paid", 0.54)),         # within max(Rs1, 0.5%)
    (4599.0, 4599.0, "2026-10-12", "2026-09-22", TODAY, ("paid", 0.0)),
    (13836.0, 0.0, "2026-10-21", "2026-10-01", TODAY, ("unpaid", 13836.0)),
    (13836.0, 5000.0, "2026-10-21", "2026-10-01", TODAY, ("partial", 8836.0)),
    (13836.0, 5000.0, "2026-10-21", "2026-10-01", date(2026, 10, 22), ("overdue", 8836.0)),
    (13836.0, 0.0, "2026-10-21", "2026-10-01", date(2026, 10, 21), ("unpaid", 13836.0)),   # due today is not overdue
    (179.68, 0.0, "2026-09-08", "2026-08-19", TODAY, ("not_confirmed", 179.68)),   # 61 days, no payment at all
    (179.68, 0.0, "2026-09-08", "2026-08-19", date(2026, 9, 20), ("overdue", 179.68)),  # 32 days: still overdue
    (0.0, 0.0, None, "2026-08-19", TODAY, ("no_dues", 0.0)),
    (-199.0, 0.0, "2026-01-01", "2025-12-14", TODAY, ("no_dues", 0.0)),          # credit balance
])
def test_payment_status(amount_due, paid, due, close, today, expect):
    assert cs.payment_status(amount_due, paid, due, close, today) == expect
    assert expect[0] in cs.PAYMENT_STATUSES


# --- H2: net, not gross -------------------------------------------------------------------------------------

def test_cashback_reduced_bill_paid_in_full_is_paid(conn, make_card):
    make_card("HDFC", "9434", name="Swiggy", statement_day=22, due_day=12)
    debits = [("2026-08-24", 932, "RAZ*Swiggy"), ("2026-08-25", 435, "RAZ*Swiggy"), ("2026-08-26", 542, "PYU*Swiggy Food"),
              ("2026-08-28", 1457, "PYU*Swiggy Food"), ("2026-09-01", 344, "SWIGGY INSTAMART"), ("2026-09-03", 280, "RAZ*Swiggy"),
              ("2026-09-05", 500, "RAZ*Swiggy"), ("2026-09-08", 220, "RAZ*Swiggy"), ("2026-09-12", 200, "RAZ*Swiggy"),
              ("2026-09-20", 200, "RAZ*Swiggy")]
    for on, amt, desc in debits:
        line(conn, "9434", on, amt, desc)
    for on, amt in [("2026-08-25", 99.4), ("2026-08-26", 27.8), ("2026-08-27", 122.5), ("2026-08-28", 47.7),
                    ("2026-08-29", 25.9), ("2026-08-30", 57.5), ("2026-09-01", 97.2), ("2026-09-02", 33.0)]:
        line(conn, "9434", on, amt, "10% Swiggy CashBack", kind="credit")
    cs.backfill_statements_logic(conn)
    cs.reconcile_statements_logic(conn=conn)
    cs.record_card_payment(conn, "9434", 4599.0, "2026-10-07", "sms")
    c = summary(conn)["9434"]
    ls = c["latest_statement"]
    assert (ls["total"], ls["computed_total"], ls["stated_total"], ls["amount_due"]) == (5110.0, 4599.0, None, 4599.0)
    assert (c["payment_status"], c["is_paid"], c["remaining_due"], c["current_cycle"]) == ("paid", True, 0.0, True)
    assert (ls["due_date"], ls["due_date_source"]) == ("2026-10-12", "estimated")


def test_latest_statement_is_chosen_by_close_date_not_label(conn, make_card):
    make_card("HDFC", "9434", statement_day=22, due_day=12)
    add_statement(conn, "9434", "2026-07-23", "2026-08-22", stated=17945.0)
    add_statement(conn, "9434", "2026-08-23", "2026-09-22", stated=4599.0, month=12, year=2025)   # mislabelled row
    add_statement(conn, "9434", "2026-09-23", "2026-10-22", stated=999.0)       # closes in the future: not a bill yet
    assert summary(conn)["9434"]["latest_statement"]["billing_end"] == "2026-09-22"


# --- H1: the bank's due date -----------------------------------------------------------------------------------

def test_bank_due_date_is_used_and_estimate_is_labelled(conn, make_card):
    make_card("Axis", "9320", statement_day=14, due_day=5)
    make_card("HDFC", "4956", statement_day=1, due_day=21)
    cs.record_bank_statement(conn, "9320", 1051.38, "2026-11-01", "sms-axis", min_due=100)
    add_statement(conn, "4956", "2026-09-02", "2026-10-01", computed=13836.54, month=9)
    s = summary(conn)
    ls = s["9320"]["latest_statement"]
    assert (ls["due_date"], ls["due_date_source"], ls["min_due"], ls["amount_due"]) == ("2026-11-01", "bank", 100.0, 1051.38)
    assert (s["9320"]["payment_status"], s["9320"]["remaining_due"]) == ("unpaid", 1051.38)
    ls = s["4956"]["latest_statement"]
    assert (ls["due_date"], ls["due_date_source"]) == ("2026-10-21", "estimated")


def test_cred_relayed_due_date_counts_as_the_banks(conn, make_card):
    make_card("HDFC", "4956", statement_day=1, due_day=21)
    cs._upsert_stated_statement(conn, "4956", 13836.0, "2026-10-21", "email-cred")
    ls = summary(conn)["4956"]["latest_statement"]
    assert (ls["due_date"], ls["due_date_source"], ls["due_date_origin"], ls["total_source"]) == \
        ("2026-10-21", "bank", "cred", "email-cred")


# --- M11: no more lifetime-ratio "paid" ---------------------------------------------------------------------------

def test_old_statement_with_no_payment_is_not_confirmed(conn, make_card):
    make_card("SBI", "2971", name="AURUM", statement_day=19, due_day=8)
    add_statement(conn, "2971", "2026-01-20", "2026-02-19", stated=830.0)
    cs.record_card_payment(conn, "2971", 830.0, "2026-02-09", "sms")          # lifetime: everything ever billed was paid
    line(conn, "2971", "2026-08-01", 179.68, "BIGTREEENTERTAINMENTPR")
    add_statement(conn, "2971", "2026-07-20", "2026-08-19", computed=179.68, total=179.68)
    c = summary(conn)["2971"]
    assert (c["payment_status"], c["is_paid"], c["current_cycle"]) == ("not_confirmed", False, False)
    # the bank's statement email said "Total amount due 0.00 ... NO PAYMENT REQUIRED"
    cs.record_bank_statement(conn, "2971", 0.0, None, "email-sbi", statement_date_iso="2026-08-20")
    c = summary(conn)["2971"]
    assert (c["payment_status"], c["is_paid"], c["latest_statement"]["amount_due"]) == ("no_dues", True, 0.0)


# --- unbilled -------------------------------------------------------------------------------------------------------

def test_unbilled_counts_email_spends_once_and_shows_credits_separately(conn, make_card):
    make_card("SBI", "2111", statement_day=19, due_day=8)
    add_statement(conn, "2111", "2026-08-20", "2026-09-19", stated=46088.0)
    line(conn, "2111", "2026-09-19", 999.0, "INDLinkedInPGSI", source="sms")              # on the close date: billed
    line(conn, "2111", "2026-10-05", 357.0, "SWIGGY INSTAMART", source="sms")
    line(conn, "2111", "2026-10-11", 287.0, "BLINKIT", source="email")                     # only an SBI email alert
    line(conn, "2111", "2026-10-05", 357.0, "SWIGGY INS", source="email", dup=True)        # email copy of the SMS
    line(conn, "2111", "2026-10-12", 551.0, "SWIGGY FOOD reversal/cashback", kind="credit", source="sms")
    u = summary(conn)["2111"]["unbilled"]
    assert (u["total"], u["count"], u["credits"]) == (644.0, 2, {"total": 551.0, "count": 1})


# --- add-on cards ----------------------------------------------------------------------------------------------------

def test_addon_card_points_at_its_primary_and_its_payments_count_there(conn, make_card):
    make_card("ICICI", "1019", statement_day=21, due_day=10)
    make_card("ICICI", "8013", statement_day=21, due_day=10)
    conn.execute("UPDATE cards SET billing_group='sapphiro_group' WHERE last4 IN ('1019', '8013')")
    add_statement(conn, "1019", "2025-10-22", "2025-11-21", stated=11811.47, due_date="2025-12-08", due_src="bank")
    conn.execute("INSERT INTO cc_payments (card_last4, amount, payment_date, source) VALUES ('8013', 11811.47, '2025-12-13', 'sms')")
    s = summary(conn, today=date(2025, 12, 20))
    assert (s["1019"]["payment_status"], s["1019"]["payment"]["total"]) == ("paid", 11811.47)
    assert (s["8013"]["addon_of"], s["8013"]["latest_statement"], s["8013"]["payment_status"], s["8013"]["is_paid"]) == \
        ("1019", None, "no_dues", True)
    assert "addon_of" not in s["1019"]


# --- the routes -------------------------------------------------------------------------------------------------------

def test_routes_on_a_fresh_database(conn, make_card):
    card = make_card("Axis", "9320", statement_day=14, due_day=5)
    cs.record_bank_statement(conn, "9320", 1051.38, "2026-11-01", "sms-axis", min_due=100)
    out = rc.cards_summary()                          # used to read the long-gone sms_statement_dues table
    c = out["cards"][0]
    for key in ("payment_status", "remaining_due", "current_cycle", "is_paid", "unbilled", "latest_statement",
                "active_emis", "total_spend", "total_txns", "payment"):
        assert key in c
    for key in ("month", "year", "total", "txn_count", "billing_start", "billing_end", "due_date", "due_date_source",
                "amount_due", "stated_total", "computed_total", "min_due"):
        assert key in c["latest_statement"]
    rows = rc.card_statements(card)["statements"]
    assert rows[0]["amount_due"] == 1051.38 and rows[0]["due_date"] == "2026-11-01" and rows[0]["due_date_source"] == "bank"
    assert rows[0]["min_due"] == 100.0 and "computed_total" in rows[0] and "stated_total" in rows[0]
