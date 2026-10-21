"""iter-18 foundation: IST time helpers, kinds, quarantine, card config file (M8, HP4, HP1, M10)."""
import json
from datetime import date

from util.timez import ist_date_of, infer_year, utc_naive_iso_from_epoch
from modules.txn_store import derive_kind
from modules import quarantine
import db


# --- M8: calendar dates are IST, storage stays UTC -------------------------------------------

def test_utc_storage_format_is_unchanged():
    # 2026-10-19 14:15:07.095 UTC as epoch ms
    assert utc_naive_iso_from_epoch(1792419307095) == "2026-10-19T14:15:07.095000"


def test_after_midnight_ist_is_the_next_day():
    assert ist_date_of("2026-10-31T19:30:00") == "2026-11-01"      # 01:00 IST on Nov 1
    assert ist_date_of("2026-10-31 18:29:59") == "2026-10-31"      # 23:59:59 IST
    assert ist_date_of("2026-10-31T19:30:00+00:00") == "2026-11-01"
    assert ist_date_of(None) is None and ist_date_of("garbage") is None


def test_year_less_dates_never_jump_into_the_future():
    assert infer_year(12, 31, date(2027, 1, 2)) == 2026            # "31DEC" read on 2 Jan
    assert infer_year(10, 13, date(2026, 10, 19)) == 2026
    assert infer_year(10, 22, date(2026, 10, 19)) == 2026          # within the grace window
    assert infer_year(11, 30, date(2026, 10, 19)) == 2025


# --- HP4: kind is what happened, independent of category --------------------------------------

def test_kinds():
    assert derive_kind("credit", "Food & Dining", "SWIGGY", "cc", pattern_kind="refund") == "refund"
    assert derive_kind("credit", "Food & Dining", "Reversal of Rs 4.01", "cc") == "reversal"
    assert derive_kind("credit", "Shopping", "CASHBACK CREDIT", "card") == "cashback"
    assert derive_kind("credit", "Uncategorized", "SOME CREDIT", "card") == "refund"
    assert derive_kind("credit", "Transfer", "salary", "bank") == "transfer_in"
    assert derive_kind("credit", "Finance", "PAYMENT RECEIVED", "card") == "bill_payment"
    assert derive_kind("debit", "EMI", "PRINCIPAL AMOUNT AMORTIZATION", "card") == "emi_principal"
    assert derive_kind("debit", "EMI", "INTEREST ON EMI", "card") == "interest"
    assert derive_kind("debit", "Bank Charges", "LATE PAYMENT FEE", "card") == "fee"
    assert derive_kind("debit", "Shopping", "ZUDIO", "card") == "purchase"
    assert derive_kind("debit", "Transfer", "Ramesh", "upi") == "debit"
    assert derive_kind("debit", "ATM Withdrawal", "ATM", "atm") == "atm"


# --- HP1: quarantine ----------------------------------------------------------------------------

def test_money_movement_detection():
    assert quarantine.looks_like_money_movement(
        "Spent INR 979 \nusing Axis Bank Credit Card no. XX9320 on 19-10-26 01:49:17 IST at \nAIRTEL PAYM.")
    assert not quarantine.looks_like_money_movement("Your OTP is 123456. Do not share.")
    assert not quarantine.looks_like_money_movement("Congratulations! You won a free trip")


def test_quarantine_is_idempotent_and_grouped(conn):
    body = "Spent INR 49 using Axis Bank Credit Card no. XX9320 on 16-10-26 17:10:34 IST at AIRTEL PAYM."
    quarantine.quarantine(conn, "sms", 5640, "AD-AXISBK-S", body, "no_pattern")
    quarantine.quarantine(conn, "sms", 5640, "AD-AXISBK-S", body, "no_pattern")
    quarantine.quarantine(conn, "sms", 5688, "AD-AXISBK-S", body.replace("49", "979"), "no_pattern")
    s = quarantine.summary(conn)
    assert s["open"] == 2 and s["by_bank"] == {"Axis": 2}
    assert len(s["templates"]) == 1 and s["templates"][0]["n"] == 2
    quarantine.resolve(conn, "sms", 5640, "parsed", transaction_id=1)
    assert quarantine.summary(conn)["open"] == 1


# --- M10: card config lives in the data directory ----------------------------------------------

def test_cards_config_round_trip_and_no_silent_reactivation(conn, make_card, tmp_path):
    make_card("HDFC", "9434", name="Swiggy", statement_day=22, due_day=12)
    make_card("HDFC", "6611", name="Account", active=0)
    path = str(tmp_path / "cards.json")
    assert db.sync_cards_config(path) == {"created": 2}                 # first run exports
    cfg = json.load(open(path))
    assert {c["last4"]: c["active"] for c in cfg["cards"]} == {"9434": 1, "6611": 0}
    # owner edits the file: new due day, a replacement card
    for c in cfg["cards"]:
        if c["last4"] == "9434":
            c["due_day"] = 10
    cfg["cards"].append({"bank": "HDFC", "last4": "1111", "card_name": "Swiggy (reissued)", "active": 1,
                         "statement_day": 22, "due_day": 10})
    json.dump(cfg, open(path, "w"))
    assert db.sync_cards_config(path) == {"applied": 3}
    rows = {r["last4"]: dict(r) for r in conn.execute("SELECT last4, due_day, active FROM cards")}
    assert rows["9434"]["due_day"] == 10 and rows["1111"]["active"] == 1
    assert rows["6611"]["active"] == 0                                  # never re-activated
