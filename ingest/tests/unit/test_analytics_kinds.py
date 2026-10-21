"""kind for history (iter-18 HP4 / H5b): modules.kinds_backfill and the manual-override path.

Message texts are real wordings from samples.txt / the live DB (amounts and names changed)."""
from modules.kinds_backfill import backfill_kinds, kind_for_row
from modules.categorizer import card_numbers
from modules.txn_store import derive_kind
from routes.transactions import update_transaction

HDFC_REFUND = ("Alert! Rs. 2 refunded by SWIGGY FOOD            BANGALORE     IND on 03/OCT/2026 & adjusted "
               "against HDFC Bank Credit Card 9434 View updated balance here: https://1.hdfc.bank.in/HDFCBK/s/mqzzz017")
SBI_REVERSAL_CASHBACK = ("Rs. 551 has been credited to your SBI Credit Card xxxx2111, towards reversal/cashback "
                         "from SWIGGY FOOD            BANGALORE     IN for trxn. dated 18/09/2026")
AXIS_CASHBACK = ("Congratulations! Cashback of INR 141 has been credited to your Airtel Axis Bank Credit Card "
                 "XX9320 towards your spends during AUG'26.")
UPI_REVERSED = "Your UPI transaction of Rs. 280.00 has been reversed"
ICICI_SPEND_WITH_EMI_UPSELL = (
    "Rs 12,603.00 spent on ICICI Bank Card XX1019 on 29-Mar-25 at CROMA. Avl Lmt: Rs 2,51,888.87. To dispute, "
    "call 18002662/SMS BLOCK 1019 to 9215676766. To convert this txn to EMI give a missed call on 9537667667. "
    "Know more about EMI conversion")


def row(conn, desc, category, source="sms", card="", txn_type="debit", raw_text=None, amount=100.0, kind=None):
    cur = conn.execute(
        "INSERT INTO transactions (amount, raw_description, merchant, category, date, source, card_last4, "
        "txn_type, confidence, raw_text, kind) VALUES (?, ?, ?, ?, '2026-10-05', ?, ?, ?, 'high', ?, ?)",
        (amount, desc, desc, category, source, card, txn_type, raw_text, kind),
    )
    conn.commit()
    return cur.lastrowid


def kind_of(conn, txn_id):
    return conn.execute("SELECT kind FROM transactions WHERE id = ?", (txn_id,)).fetchone()["kind"]


def cards(conn, make_card):
    make_card("HDFC", "9434", name="Swiggy")
    make_card("SBI", "2111", name="Cashback")
    make_card("Axis", "9320", name="Airtel")
    make_card("ICICI", "1019", name="Sapphiro")
    make_card("HDFC", "6611", name="Account", active=0)


def test_credit_kinds_read_the_full_message(conn, make_card):
    cards(conn, make_card)
    # an account reversal: the description is a placeholder, only the SMS says "reversed"
    rev = row(conn, "Unknown credit", "Uncategorized", card="6611", txn_type="credit", raw_text=UPI_REVERSED)
    sbi = row(conn, "SWIGGY FOOD BANGALORE", "Food & Dining", card="2111", txn_type="credit",
              raw_text=SBI_REVERSAL_CASHBACK)
    axis = row(conn, "Axis credit", "Uncategorized", card="9320", txn_type="credit", raw_text=AXIS_CASHBACK)
    hdfc = row(conn, "SWIGGY FOOD BANGALORE", "Food & Dining", card="9434", txn_type="credit", raw_text=HDFC_REFUND)
    c = card_numbers(conn)
    got = {k: kind_for_row(conn.execute("SELECT * FROM transactions WHERE id=?", (i,)).fetchone(), c)
           for k, i in (("rev", rev), ("sbi", sbi), ("axis", axis), ("hdfc", hdfc))}
    assert got == {"rev": "reversal", "sbi": "cashback", "axis": "cashback", "hdfc": "refund"}
    # on the description alone the account reversal would have been money coming in
    assert derive_kind("credit", "Uncategorized", "Unknown credit", "bank") == "transfer_in"


def test_debit_kinds_ignore_bank_boilerplate(conn, make_card):
    """ICICI appends an EMI-conversion upsell to every card spend SMS."""
    cards(conn, make_card)
    croma = row(conn, "CROMA", "Shopping", card="1019", raw_text=ICICI_SPEND_WITH_EMI_UPSELL, amount=12603)
    r = conn.execute("SELECT * FROM transactions WHERE id=?", (croma,)).fetchone()
    assert kind_for_row(r, card_numbers(conn)) == "purchase"
    assert derive_kind("debit", "Shopping", ICICI_SPEND_WITH_EMI_UPSELL, "cc") == "emi_principal"


def test_backfill_dry_run_then_apply_only_null_kinds(conn, make_card):
    cards(conn, make_card)
    ids = {
        "refund": row(conn, "SWIGGY FOOD BANGALORE", "Food & Dining", card="9434", txn_type="credit",
                      raw_text=HDFC_REFUND, amount=2),
        "principal": row(conn, "Principal Amount Amortization - <2/6>AMAZON PAY IN", "EMI", source="card",
                         card="1019", amount=4540),
        "interest": row(conn, "Interest Amount Amortization - <2/6>AMAZON PAY IND", "EMI", source="card",
                        card="1019", amount=311),
        "fee": row(conn, "LATE PAYMENT FEE", "Bank Charges", source="card", card="9434", amount=500),
        "payment": row(conn, "CREDIT CARD PAYMENT", "Finance", source="card", card="9434", txn_type="credit",
                       amount=4599),
        "cred": row(conn, "CRED Club", "Finance", card="6611", amount=15000),
        "atm": row(conn, "HDFC ATM", "ATM Withdrawal", card="6611", amount=2000),
        "person": row(conn, "RAMESH KUMAR", "Transfer", card="6611", amount=700),
        "spend": row(conn, "CROMA", "Shopping", card="1019", raw_text=ICICI_SPEND_WITH_EMI_UPSELL, amount=12603),
        "salary": row(conn, "HSBC credit", "Uncategorized", txn_type="credit", amount=85000,
                      raw_text="Your HSBC A/c XXXXXX4706 has been credited with INR 85,000.00 on 01OCT."),
        "already": row(conn, "SOMETHING", "Shopping", card="9434", kind="refund"),
    }
    expected = {"refund": "refund", "principal": "emi_principal", "interest": "interest", "fee": "fee",
                "payment": "bill_payment", "cred": "bill_payment", "atm": "atm", "person": "debit",
                "spend": "purchase", "salary": "transfer_in"}

    dry = backfill_kinds(conn, dry_run=True)
    assert dry["rows"] == 10 and dry["updated"] == 0
    assert dry["by_kind"] == {"bill_payment": 2, "refund": 1, "emi_principal": 1, "interest": 1, "fee": 1,
                              "atm": 1, "debit": 1, "purchase": 1, "transfer_in": 1}
    assert dry["by_type_kind"]["credit:bill_payment"] == 1 and dry["by_type_kind"]["debit:bill_payment"] == 1
    assert dry["amount_by_kind"]["bill_payment"] == 19599
    assert all(kind_of(conn, i) is None for k, i in ids.items() if k != "already")

    done = backfill_kinds(conn, dry_run=False)
    assert done["updated"] == 10
    assert {k: kind_of(conn, ids[k]) for k in expected} == expected
    assert kind_of(conn, ids["already"]) == "refund"                 # never overwritten
    assert backfill_kinds(conn, dry_run=True)["rows"] == 0


def test_manual_override_moves_category_derived_kind_with_it(conn, make_card):
    """A Finance row re-filed as Shopping must start counting; a refund re-filed stays a refund."""
    cards(conn, make_card)
    cred = row(conn, "CRED Club", "Finance", card="6611", amount=15000, kind="bill_payment")
    refund = row(conn, "SWIGGY FOOD BANGALORE", "Food & Dining", card="9434", txn_type="credit",
                 raw_text=HDFC_REFUND, kind="refund")
    update_transaction(cred, {"category": "Shopping"})
    update_transaction(refund, {"category": "Groceries"})
    assert kind_of(conn, cred) == "debit"
    assert kind_of(conn, refund) == "refund"
    payment = row(conn, "PAYMENT ADJ", "Shopping", source="card", card="9434", txn_type="credit", kind="refund")
    update_transaction(payment, {"category": "Finance"})
    assert kind_of(conn, payment) == "refund"                       # text-derived kinds stay
