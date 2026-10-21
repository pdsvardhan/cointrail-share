"""recategorize_with_context — re-running the categorizer over history (iter-18 M14).

Rows are written with raw INSERTs carrying the labels the pre-iter-18 categorizer gave them, the
way they sit in the live database."""
from modules.categorizer import context_for_row, card_numbers, recategorize_with_context
from routes.transactions import recategorize_all

ICICI_ZUDIO = ("INR 9,648.00 spent using ICICI Bank Card XX0026 on 08-Sep-26 on ZUDIO. "
               "Avl Limit: INR 2,41,000.00. If not you, call 1800 2662/SMS BLOCK 0026 to 9215676766.")
HDFC_SENT = ("Sent Rs.2000.00 From HDFC Bank A/C *6611 To RAMESH KUMAR On 08/10/26 Ref 600000000017 "
             "Not You? Call 18002586161/SMS BLOCK UPI to 7308080808")


def old_row(conn, desc, category, merchant=None, source="sms", card="", txn_type="debit",
            confidence="medium", raw_text=None, amount=100.0, date="2026-09-08"):
    cur = conn.execute(
        "INSERT INTO transactions (amount, raw_description, merchant, category, date, source, card_last4, "
        "txn_type, confidence, raw_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (amount, desc, merchant or desc, category, date, source, card, txn_type, confidence, raw_text),
    )
    conn.commit()
    return cur.lastrowid


def label(conn, txn_id):
    r = conn.execute("SELECT merchant, category, confidence FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    return r["merchant"], r["category"]


def setup_history(conn, make_card):
    make_card("ICICI", "0026", name="Amazon Pay")
    make_card("SBI", "2111", name="Cashback", pdf_card_last4="9181")
    make_card("HDFC", "6611", name="Account", active=0)        # a bank account, not a card
    ids = {
        "zudio": old_row(conn, "ZUDIO", "Transfer", source="sms", card="0026", raw_text=ICICI_ZUDIO),
        "trent": old_row(conn, "TRENT LIMITED", "Rent & Housing", source="card", card="0026"),
        "rama": old_row(conn, "RAMA FABRICS", "Transfer", source="sms", card="0026"),
        "swiggy_addr": old_row(conn, "SY NOS 8 1 TO 55 6 DEVARA", "Rent & Housing", merchant="Rent/Housing",
                               source="sms", card="0026", confidence="high"),
        "hsbc_credit": old_row(conn, "HSBC credit", "Finance", merchant="CRED", txn_type="credit",
                               confidence="high"),
        "mandate": old_row(conn, "IND*LinkedIn against E-mandate (SiHub ID - Xqzzzzz001) registered by you at merchant",
                           "ATM Withdrawal", merchant="ATM Withdrawal", source="sms", card="2111"),
        "person": old_row(conn, "RAMESH KUMAR", "Uncategorized", source="sms", card="6611", raw_text=HDFC_SENT),
        "unchanged": old_row(conn, "Swiggy", "Food & Dining", merchant="Swiggy", card="0026", confidence="high"),
        # protected rows: categorize() would change every one of them
        "manual": old_row(conn, "TRENT LIMITED", "Rent & Housing", source="card", card="0026", confidence="manual"),
        "overridden": old_row(conn, "ZUDIO", "Transfer", source="sms", card="0026"),
        "enriched": old_row(conn, "BUNDL TECHNOLOGIES", "Groceries", merchant="Swiggy Instamart",
                            card="0026", confidence="enriched"),
        "upi_mapped": old_row(conn, "shopx.rzp@hdfcbank (UPI 600000000020)", "Shopping", merchant="ShopX",
                              card="6611", confidence="high"),
    }
    conn.execute("INSERT INTO category_overrides (transaction_id, old_category, new_category) VALUES (?, ?, ?)",
                 (ids["overridden"], "Shopping", "Transfer"))
    conn.execute("INSERT INTO upi_mappings (upi_id, mapped_type, brand, category) VALUES "
                 "('shopx.rzp@hdfcbank', 'merchant', 'ShopX', 'Shopping')")
    conn.commit()
    return ids


EXPECTED_TRANSITIONS = {
    "Rent & Housing→Shopping": 1,           # TRENT
    "Transfer→Shopping": 2,                 # RAMA FABRICS, and ZUDIO (a named shop, no longer a "person")
    "Rent & Housing→Food & Dining": 1,      # Swiggy's address
    "Finance→Uncategorized": 1,             # HSBC credit ("cred" in "credit")
    "ATM Withdrawal→Subscriptions": 1,      # e-mandate "at merchant"
    "Uncategorized→Transfer": 1,            # a person paid from the (inactive) bank account
}


def test_context_comes_from_the_row(conn, make_card):
    setup_history(conn, make_card)
    cards = card_numbers(conn)
    assert cards == {"0026", "2111", "9181"}
    assert context_for_row("card", "0026", None, cards) == "card"
    assert context_for_row("sms", "0026", ICICI_ZUDIO, cards) == "cc"
    assert context_for_row("email", "9181", None, cards) == "email-card"
    assert context_for_row("sms", "6611", HDFC_SENT, cards) == "upi"           # account, UPI wording
    assert context_for_row("sms", "6611", "HDFC ATM", cards) == "bank"


def test_dry_run_reports_transitions_and_writes_nothing(conn, make_card):
    ids = setup_history(conn, make_card)
    before = {k: label(conn, v) for k, v in ids.items()}

    res = recategorize_with_context(conn, dry_run=True)

    assert res["by_transition"] == EXPECTED_TRANSITIONS
    assert res["changed"] == 7 and res["updated"] == 0 and res["dry_run"] is True
    assert res["skipped"] == {"manual": 2, "enriched": 1, "upi_mapping": 1}
    assert res["scanned"] == 12
    sample = res["samples"]["Rent & Housing→Shopping"][0]
    assert sample["id"] == ids["trent"] and sample["context"] == "card"
    assert sample["merchant"] == "Trent (Westside/Zudio)"
    assert {k: label(conn, v) for k, v in ids.items()} == before


def test_apply_changes_labels_and_keeps_protected_rows(conn, make_card):
    ids = setup_history(conn, make_card)
    protected = {k: label(conn, ids[k]) for k in ("manual", "overridden", "enriched", "upi_mapped", "unchanged")}

    res = recategorize_with_context(conn, dry_run=False)

    assert res["changed"] == 7 and res["updated"] == 7
    assert label(conn, ids["zudio"]) == ("Zudio", "Shopping")
    assert label(conn, ids["trent"]) == ("Trent (Westside/Zudio)", "Shopping")
    assert label(conn, ids["rama"]) == ("RAMA FABRICS", "Shopping")
    assert label(conn, ids["swiggy_addr"]) == ("Swiggy", "Food & Dining")
    assert label(conn, ids["hsbc_credit"]) == ("HSBC credit", "Uncategorized")
    assert label(conn, ids["mandate"]) == ("LinkedIn", "Subscriptions")
    assert label(conn, ids["person"]) == ("RAMESH KUMAR", "Transfer")
    assert {k: label(conn, ids[k]) for k in protected} == protected
    # converges: a second pass has nothing left to do
    again = recategorize_with_context(conn, dry_run=True)
    assert again["changed"] == 0 and again["merchant_renamed"] == 0


def test_merchant_only_change_is_counted_separately(conn, make_card):
    make_card("HDFC", "9434", name="Swiggy")
    rid = old_row(conn, "Razorpay*Swiggy LimiteBengaluru", "Food & Dining", merchant="Razorpay",
                  source="card", card="9434", confidence="high")
    res = recategorize_with_context(conn, dry_run=False)
    assert res["changed"] == 0 and res["merchant_renamed"] == 1 and res["updated"] == 1
    assert label(conn, rid) == ("Swiggy", "Food & Dining")


def test_recategorize_all_endpoint_is_a_dry_run_unless_asked(conn, make_card):
    ids = setup_history(conn, make_card)
    res = recategorize_all()
    assert res["dry_run"] is True and res["by_transition"] == EXPECTED_TRANSITIONS
    assert label(conn, ids["trent"])[1] == "Rent & Housing"
    res = recategorize_all(dry_run=False)
    assert res["updated"] == 7
    assert label(conn, ids["trent"])[1] == "Shopping"
