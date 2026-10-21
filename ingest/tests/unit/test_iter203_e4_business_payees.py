"""iter-203 E4 — businesses paid from a bank account by UPI are spending, not 'Sent to people'.

HDFC prints only the payee ("Sent Rs.470.00 From HDFC Bank A/C *1111 To SVC CATERINGS On ..."), and
the person-name heuristic filed every two-word business as a Transfer. A payee is now a business when
its name carries a business word (caterings, health care, juice center, traders, pvt, ...), its UPI ID
has a merchant shape (paytmqr*, q<digits>@ybl, vyapar.*, *.rzp, okbiz*), or the bank says it is a UPI
AutoPay mandate (only merchants can hold one). Names and IDs are made up."""
import pytest

from modules.categorizer import categorize
from modules.sms.parser import parse_single_sms
from modules.txn_store import insert_transaction
from routes.analytics import category_group
from routes.transactions import update_transaction
from scripts.iter203_be2_steps import step_recategorize_sent_to_people


@pytest.mark.parametrize("desc, ctx, expected", [
    ("SVC CATERINGS", "upi", "Food & Dining"),
    ("Snabbit", "upi", "Household help"),
    ("SNABBIT", "upi", "Household help"),
    ("MyGate", "upi", "Rent & Housing"),
    ("Story TV", "upi_mandate", "Subscriptions"),
    ("Google", "upi_mandate", "Uncategorized"),
    ("ACME MEDIA", "upi_mandate", "Uncategorized"),          # any mandate payee is a merchant
    ("M S ACME HEALTH CARE", "upi", "Health"),
    ("RAVI JUICE CENTER", "upi", "Food & Dining"),
    ("Prakash TEA POINT", "upi", "Food & Dining"),
    ("Sri ganesh chat bhandar", "upi", "Food & Dining"),
    ("Transferred to ACME GENERAL INSURANC", "bank", "Insurance"),
    ("ACME TRADERS", "upi", "Uncategorized"),                 # a business with no category word
    ("NORTHWIND TECHNOLOGIES PVT LTD", "upi", "Uncategorized"),
    ("q900000017@ybl", "upi", "Uncategorized"),
    ("paytmqr1abc23@ptys", "upi", "Uncategorized"),
    ("paytm-10000017@ptys", "upi", "Uncategorized"),
    ("vyapar.100000000017@hdfcbank", "upi", "Uncategorized"),
    ("northwind.rzp@axisbank", "upi", "Uncategorized"),
    ("gpay-11223344@okbizaxis", "upi", "Uncategorized"),
])
def test_business_payees_paid_by_upi_count_as_spending(conn, desc, ctx, expected):
    _merchant, category, _conf = categorize(desc, context=ctx)
    assert category == expected
    assert category_group(category) == "spend"


@pytest.mark.parametrize("desc, ctx", [
    ("RAMESH KUMAR", "upi"),
    ("Mr Ravi Teja Reddy", "upi"),
    ("Lakshmi  Bai", "upi"),          # doubled space as banks print it
    ("NEHA", "bank"),
    ("ramesh77@okaxis", "upi"),
    ("9876543210@ptyes", "upi"),          # a phone-number UPI ID is a person's
    ("thanksforcalling0000-2@okaxis", "upi"),
])
def test_people_stay_sent_to_people(conn, desc, ctx):
    assert categorize(desc, context=ctx)[1] == "Transfer"


def test_named_wallet_aliases_keep_their_own_category(conn):
    # a business word must not switch off an alias that names the payee
    assert categorize("PHONEPE PVT LTD", context="upi")[:2] == ("PhonePe", "Transfer")


def test_hdfc_upi_mandate_sms_carries_the_mandate_context(conn):
    body = ("UPI Mandate:\nSent Rs.599.00\nfrom HDFC Bank A/c 1111\nTo ACME MEDIA\n10/08/26\n"
            "Ref 600000000001\nNot You? Call 18002586161/SMS BLOCK UPI to 7308080808\n")
    parsed = parse_single_sms(body)
    assert parsed["source"] == "upi_mandate" and parsed["description"] == "ACME MEDIA"
    t = insert_transaction(conn, amount=599.0, date="2026-08-10", source="sms", source_ref="m-1",
                           raw_description="ACME MEDIA", raw_text=body, context=parsed["source"], link=False)
    assert t["category"] == "Uncategorized" and t["kind"] == "debit"


def _old(conn, desc, category="Transfer", source="sms", amount=100.0, raw_text=None):
    sent = (f"Sent Rs.{amount:.2f} From HDFC Bank A/C *1111 To {desc} On 08/10/26 Ref 600000000017 "
            f"Not You? Call 18002586161/SMS BLOCK UPI to 7308080808")
    cur = conn.execute(
        "INSERT INTO transactions (amount, raw_description, merchant, category, date, source, card_last4, "
        "txn_type, confidence, raw_text) VALUES (?, ?, ?, ?, '2026-09-26', ?, '', 'debit', 'medium', ?)",
        (amount, desc, desc, category, source, raw_text if raw_text is not None else sent),
    )
    conn.commit()
    return cur.lastrowid


def test_the_data_step_moves_business_rows_out_and_leaves_people_and_hand_filed_rows(conn, make_card):
    make_card("HDFC", "1111", name="Account", active=0)
    caterer = _old(conn, "SVC CATERINGS", amount=470.0)
    snab = _old(conn, "Snabbit", amount=240.45)
    person = _old(conn, "RAMESH KUMAR", amount=2000.0)
    hand = _old(conn, "ACME TRADERS", amount=300.0)
    update_transaction(hand, {"category": "Transfer"})          # the owner said this one is a transfer

    dry = step_recategorize_sent_to_people(conn, dry_run=True)
    assert dry["dry_run"] is True and dry["rows_moved"] == 2
    assert {r["id"] for r in dry["rows"]} == {caterer, snab}
    assert conn.execute("SELECT category FROM transactions WHERE id = ?", (caterer,)).fetchone()[0] == "Transfer"

    res = step_recategorize_sent_to_people(conn, dry_run=False)
    assert res["rows_moved"] == 2 and res["live_amount_moved"] == pytest.approx(710.45)
    cats = {r["id"]: r["category"] for r in conn.execute("SELECT id, category FROM transactions").fetchall()}
    assert cats[caterer] == "Food & Dining" and cats[snab] == "Household help"
    assert cats[person] == "Transfer" and cats[hand] == "Transfer"
    assert step_recategorize_sent_to_people(conn, dry_run=True)["rows_moved"] == 0      # converges


def test_an_sms_and_its_email_twin_move_together_in_one_pass(conn, make_card):
    make_card("HDFC", "1111", name="Account", active=0)
    live = _old(conn, "SVC CATERINGS", amount=470.0)
    twin = _old(conn, "SVC CATERINGS", source="email", amount=470.0, raw_text="You have done a UPI txn. SVC CATERINGS")
    conn.execute("UPDATE transactions SET is_duplicate = 1, matched_txn_id = ? WHERE id = ?", (live, twin))
    conn.commit()
    res = step_recategorize_sent_to_people(conn, dry_run=False)
    assert {r["id"] for r in res["rows"]} == {live, twin}
    assert step_recategorize_sent_to_people(conn, dry_run=True)["rows_moved"] == 0


@pytest.mark.parametrize("desc, ctx", [
    ("IndianRailwayCateringa", "cc"),                # how card SMS cut IRCTC's legal name
    ("IndianRailwayCateringa", "email-card"),
    ("INDIAN RAILWAY CATERING AND TOURISM CORP", "card"),
    ("IRCTC Web UPI", "upi"),
])
def test_irctc_train_tickets_stay_travel_despite_the_catering_word(conn, desc, ctx):
    # verifier round 2: the new Food keyword 'catering' (for canteens paid by UPI) took IRCTC tickets
    assert categorize(desc, context=ctx)[:2] == ("IRCTC", "Travel")


def test_a_mandate_debit_and_its_email_twin_leave_sent_to_people_together(conn, make_card):
    # verifier round 2: the e-mail twin of a 'UPI Mandate' SMS reads like a payment to a person
    # ("to VPA cloudbill@axisbank Acmecloud") and kept vouching for the old Transfer label
    make_card("HDFC", "1111", name="Account", active=0)
    sms = ("UPI Mandate:\nSent Rs.1.52\nfrom HDFC Bank A/c 1111\nTo Acmecloud\n03/01/26\n"
           "Ref 600000000002\nNot You? Call 18002586161/SMS BLOCK UPI to 7308080808\n")
    live = _old(conn, "Acmecloud", amount=1.52, raw_text=sms)
    twin = _old(conn, "Acmecloud", source="email", amount=1.52,
                raw_text="You have done a UPI txn. | Dear Customer, Rs.1.52 has been debited from account 1111 "
                         "to VPA cloudbill@axisbank Acmecloud on 03-01-26.")
    conn.execute("UPDATE transactions SET is_duplicate = 1, matched_txn_id = ? WHERE id = ?", (live, twin))
    conn.commit()
    res = step_recategorize_sent_to_people(conn, dry_run=False)
    assert {r["id"] for r in res["rows"]} == {live, twin}
    cats = {r["id"]: r["category"] for r in conn.execute("SELECT id, category FROM transactions").fetchall()}
    assert cats[live] == cats[twin] == "Uncategorized"
    assert step_recategorize_sent_to_people(conn, dry_run=True)["rows_moved"] == 0      # converges
