"""iter-18 HP1: one classification path for every SMS (realtime, batch, Gmail backup), the parse quarantine,
history reprocessing, and retiring merchant-notice refund rows (H5). Real wordings, fake names/refs."""
import pytest

from modules import quarantine
from modules.sms import processing
from modules.sms.processing import (
    process_sms_row, classify_sms_row, process_pending, retry_quarantine, reprocess_history,
    retire_merchant_refund_notice_rows,
)

AXIS_NEW = ("Spent INR 979 \nusing Axis Bank Credit Card no. XX9320 on 19-10-26 01:49:17 IST at \nAIRTEL PAYM. \n"
            "Avl limit: INR 157920.62. Not you? SMS BLOCK 9320 to +910000000001.")
SWIGGY_NOTICE = ("Refund of Rs 423.0 has been initiated for Swiggy order 248300000000001. Updated balance should "
                 "reflect in 4-7 days. https://example.invalid/r")
HDFC_REFUND = ("Alert! Rs. 423 refunded by SWIGGY FOOD            BANGALORE     IND on 14/SEP/2026 & adjusted "
               "against HDFC Bank Credit Card 9434 View updated balance here: https://example.invalid/x")


def add_sms(conn, body, sender="AD-HDFCBK-S", phone_timestamp="2026-10-18T10:00:00", is_processed=0, is_financial=1):
    cur = conn.execute(
        "INSERT INTO sms (sender, body, phone_timestamp, is_financial, is_processed) VALUES (?, ?, ?, ?, ?)",
        (sender, body, phone_timestamp, is_financial, is_processed))
    conn.commit()
    return conn.execute("SELECT * FROM sms WHERE id = ?", (cur.lastrowid,)).fetchone()


def txn_for(conn, sms_id):
    return conn.execute("SELECT * FROM transactions WHERE source='sms' AND source_ref=?", (str(sms_id),)).fetchone()


def sms_state(conn, sms_id):
    return conn.execute("SELECT is_processed FROM sms WHERE id=?", (sms_id,)).fetchone()["is_processed"]


# --- transactions ---------------------------------------------------------------------------------

def test_axis_new_format_becomes_a_card_transaction(conn, make_card):
    make_card("Axis", "9320", name="Airtel")
    row = add_sms(conn, AXIS_NEW, sender="AD-AXISBK-S", phone_timestamp="2026-10-18T20:19:30")
    out = process_sms_row(conn, row)
    assert out["class"] == "transaction" and out["status"] == "inserted"
    t = txn_for(conn, row["id"])
    assert (t["amount"], t["card_last4"], t["date"], t["txn_type"], t["bank"]) == (979.0, "9320", "2026-10-19", "debit", "Axis")
    assert t["occurred_at"] == "2026-10-19T01:49:17+05:30" and t["kind"] == "purchase"
    assert t["category"] != "Transfer"                       # card context: never the person-name heuristic
    assert sms_state(conn, row["id"]) == 1
    # idempotent: re-running never adds a second row
    again = process_sms_row(conn, row)
    assert again["status"] == "exists"
    assert conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"] == 1


def test_foreign_currency_row_keeps_original_amount(conn):
    row = add_sms(conn, "Transaction Successful! USD118.00 has been debited to your SBI Credit Card XX2111 against "
                        "recurring e-mandate registered by you at merchant site. Details: Merchant: ANTHROPICCLAUDESUB "
                        "SiHub ID: Fake9012Ef Trxn. Ref. No.: 400000000000003 Date: 25-Sep-2026. Time",
                  sender="AD-SBICRD-S", phone_timestamp="2026-09-24T18:20:00")
    process_sms_row(conn, row)
    t = txn_for(conn, row["id"])
    assert (t["currency"], t["original_amount"], t["fx_estimated"], t["ref_no"]) == ("USD", 118.0, 1, "400000000000003")
    assert t["amount"] > 10000 and t["merchant"] and t["date"] == "2026-09-25"


def test_dateless_sms_is_dated_by_its_ist_receive_day(conn, make_card):
    # received 2026-10-18 19:00 UTC = 00:30 IST on the 19th: the credit belongs to the 19th (M8)
    make_card("Axis", "9320", name="Airtel")
    row = add_sms(conn, "Congratulations! Cashback of INR 141 has been credited to your Airtel Axis Bank Credit Card "
                        "XX9320 towards your spends during SEP'26.", sender="AD-AXISBK-S",
                  phone_timestamp="2026-10-18T19:00:00")
    out = process_sms_row(conn, row)
    t = txn_for(conn, row["id"])
    assert out["class"] == "transaction"
    assert (t["date"], t["txn_type"], t["kind"], t["card_last4"]) == ("2026-10-19", "credit", "cashback", "9320")


def test_dateless_sms_falls_back_to_received_at(conn):
    row = add_sms(conn, "HDFC Bank: Your UPI transaction of Rs. 280.00 has been reversed in your account due to "
                        "technical problem (UPI Ref no. 500000000001)", phone_timestamp=None)
    conn.execute("UPDATE sms SET received_at = '2026-10-31 18:45:00' WHERE id = ?", (row["id"],))
    row = conn.execute("SELECT * FROM sms WHERE id = ?", (row["id"],)).fetchone()
    process_sms_row(conn, row)
    t = txn_for(conn, row["id"])
    assert (t["date"], t["kind"], t["ref_no"]) == ("2026-11-01", "reversal", "500000000001")


def test_year_less_body_date_uses_the_sms_receive_date(conn):
    # HSBC "on 31DEC" processed long after: the year comes from the SMS, not the server clock (H8)
    row = add_sms(conn, "HSBC: Dear Customer, your HSBC A/c 111-222***-706 has been credited with INR 1,000.00+ on "
                        "31DEC as SALARY CREDIT . Your available Bal is 1,000.00 .", sender="JM-HSBCIN-S",
                  phone_timestamp="2027-01-01T06:00:00")
    process_sms_row(conn, row)
    t = txn_for(conn, row["id"])
    assert (t["date"], t["card_last4"], t["txn_type"]) == ("2026-12-31", "706", "credit")
    # a salary is income, whatever the categorizer makes of the word "CREDIT"
    assert t["kind"] == "transfer_in"


def test_own_account_transfer_is_categorised_self_transfer(conn):
    row = add_sms(conn, "HSBC: Dear Customer, your HSBC A/c 111-222***-001 has been debited with INR 28,200.00- on "
                        "29SEP as transfer towards account 111-222***-706 . Your avl Bal is 820,091.37 .",
                  sender="JM-HSBCIN-S", phone_timestamp="2026-09-29T12:30:56")
    process_sms_row(conn, row)
    t = txn_for(conn, row["id"])
    assert (t["category"], t["card_last4"], t["date"]) == ("Self Transfer", "001", "2026-09-29")


@pytest.mark.parametrize("body,category,merchant", [
    # the counterparty is only an account number: without the rail it was an Uncategorized (= everyday) spend
    ("Dear Customer, Your a/c no. XXXXXXXX4321 is debited for Rs.450000.00 on 24-08-26 and a/c XXXXXXX706 credited "
     "(IMPS Ref no 200000000002). -SBI", "Transfer", None),
    # payee's account suffix no longer glued to the name
    ("Your Ac Xx4321 debited Rs.50,000.00 for transfer to Asha Ac Xx7777 dt 12.10.26 Ref 600000033. If not done by "
     "you, call 1800111109. YONO SBI", "Transfer", "Asha"),
    # a standing instruction into a loan account is a loan repayment
    ("Dear Customer, Standing Instruction has been successfully executed for Rs. 40,000.00 from A/c No.XXXXX004321 to "
     "Loan A/c No.XXXXX000022 on 25/08/26.  - SBI.", "Loan Repayment", None),
])
def test_account_transfers_are_described_for_the_categorizer(conn, body, category, merchant):
    row = add_sms(conn, body, sender="AD-SBIPSG-T", phone_timestamp="2026-10-12T05:00:00")
    process_sms_row(conn, row)
    t = txn_for(conn, row["id"])
    assert t["category"] == category and t["kind"] == "debit"
    if merchant:
        assert t["merchant"] == merchant


def test_sbi_cashback_card_comes_from_the_cards_table(conn, make_card):
    make_card("SBI", "4471", name="AURUM")
    make_card("SBI", "6611", name="Cashback")
    make_card("SBI", "6600", name="Cashback (old)", active=0)
    row = add_sms(conn, "It's Cashback Time! Happy to share that Rs. 714 has been credited to your CASHBACK SBI Card "
                        "account for the month of Jul'26 - SBI Card.", sender="VA-SBICRD-S")
    process_sms_row(conn, row)
    t = txn_for(conn, row["id"])
    assert (t["card_last4"], t["kind"], t["txn_type"], t["amount"]) == ("6611", "cashback", "credit", 714.0)


def test_sbi_cashback_without_a_configured_card_still_records_the_credit(conn):
    row = add_sms(conn, "It's Cashback Time! Happy to share that Rs. 714 has been credited to your CASHBACK SBI Card "
                        "account for the month of Jul'26 - SBI Card.", sender="VA-SBICRD-S")
    process_sms_row(conn, row)
    assert txn_for(conn, row["id"])["card_last4"] == ""


def test_repeat_payments_are_not_merged_by_the_batch_path(conn):
    # M4: five Rs 280 payments to one payee on one day used to collapse to one row (amount+date+description)
    bodies = [f"Sent Rs.280.00\nFrom HDFC Bank A/C *6611\nTo TEA STALL\nOn 29/06/25\nRef 51800000000{i}\nNot You?\n"
              for i in range(5)]
    for b in bodies:
        add_sms(conn, b, phone_timestamp="2025-06-29T08:00:00")
    res = process_pending(conn)
    assert res["processed"] == 5 and res["by_class"] == {"transaction": 5}
    rows = conn.execute("SELECT is_duplicate, ref_no FROM transactions").fetchall()
    assert len(rows) == 5 and sum(r["is_duplicate"] for r in rows) == 0
    assert len({r["ref_no"] for r in rows}) == 5


def test_one_alert_delivered_twice_is_linked_not_double_counted(conn):
    body = "Spent Rs.611.11 On HDFC Bank Card 9434 At EDGE CAFE ONE On 2026-10-18:14:10:00.Not You?"
    a = add_sms(conn, body, sender="VM-HDFCBK-S", phone_timestamp="2026-10-18T08:40:05")
    b = add_sms(conn, body, sender="JD-HDFCBK-S", phone_timestamp="2026-10-18T08:40:07")
    process_sms_row(conn, a)
    process_sms_row(conn, b)
    rows = conn.execute("SELECT is_duplicate, merge_rule FROM transactions ORDER BY id").fetchall()
    assert [r["is_duplicate"] for r in rows] == [0, 1] and rows[1]["merge_rule"] == "same-body"


# --- not transactions -----------------------------------------------------------------------------

@pytest.mark.parametrize("body,cls", [
    (SWIGGY_NOTICE, "noise"),
    ("Dear Swiggy customer, your refund reference number for Rs 551.0 is 270000000001-1-R1. https://example.invalid/r", "noise"),
    ("Your payment for Swiggy order #248200000000001 was not completed. Any amount if debited from your card will get "
     "refunded within 4-7 days.", "noise"),
    ("We have initiated a refund of Rs.246.00 for the cancelled order ORD50000000001 back to your card, which should "
     "reflect in 4-7 business days. -blinkit", "noise"),
    ("PNR 4800000001 ticket cancelled. Amt 460 will be refunded within 3-4 days. Please check your account/instrument "
     "from which this tkt was booked.-IRCTC", "noise"),
    ("Hi, unfortunately, your payment of Rs. 39 with Transaction ID: 7400000000000000001 for your PREPAID 9000000001 was "
     "unsuccessful. If any amount was debited, it will be refunded within 7-10 days.", "noise"),
    ("309535 is SECRET OTP for txn of INR 979.00 on Axis Bank card XX9320 at AIRTEL PAY on 19-10-26 01:48:52. OTP valid "
     "for 5 mins. Please do not share this OTP.", "noise"),
    ("<#> 265488 is the OTP to view your Axis Bank Credit Card details. Valid for 5 mins. Do not share with anyone.", "noise"),
    ("E-Mandate!\nRs.699.00 will be deducted on 04/09/26, 00:00:00\nFor SOME TECH PRIVATE LIMITED mandate\nUMN "
     "abc@ybl\nMaintain Balance\n-HDFC Bank\n", "noise"),
    ("Dear Cardholder, there is a credit balance of Rs. 630 available on your SBI Card account as on 11-Apr-2026.", "noise"),
    ("Dear Customer, your Loan A/c XXXXX000022 irregular by INR 17,570.00. Please pay immediately to avoid penal "
     "charges. Ignore if already paid. -SBI", "noise"),
    ("Some of the features and Terms and Conditions on your Axis Bank Credit Card no. XX9320 will be revised w.e.f. "
     "27-08-2026. For details, visit https://example.invalid/t", "noise"),
    ("Due to scheduled maintenance, txns. with Axis Bank Credit Card on 09-06-26, 01:30 AM - 02:30 AM IST will be "
     "paused. Inconvenience caused is regretted.", "noise"),
    ("Card Renewal Alert: Your HDFC Bank Credit Card 9434  is due for renewal. Check email to update address.", "noise"),
    ("Trxn. of Rs.399.00 done on your credit card ending 2111 without effecting add'nl factor of authentication mandated "
     "by RBI, at OPENAICHATGPTSUBSCR on 2 Nov 25 . If this Trxn. wasn't done by you, click https://example.invalid", "noise"),
    ("DEAR HDFCBANK CARDMEMBER, PAYMENT OF Rs. 4599.00 RECEIVED TOWARDS YOUR CREDIT CARD ENDING WITH 9434 ON "
     "7-10-2026.YOUR AVAILABLE LIMIT IS RS. 133844.97", "bill_payment"),
    ("We have received payment of Rs.46,088.00 via BBPS & the same has been credited to your SBI Credit Card. Your "
     "available limit is Rs.300,201.91.", "bill_payment"),
    ("Amount Due\nRs.15336 on HDFC Bank Credit Card 4956. Pay instantly by 21/JUN/2026 via PayZapp > Bill Pay > Credit "
     "Card: https://example.invalid/p", "statement"),
    ("Payment of INR 2411.32 for Axis Bank Credit Card no. XX9320 is due on 01-08-26 with minimum amount due of INR 100. "
     "Ignore if paid.", "statement"),
    ("Your Airtel Axis Bank Mastercard Credit Card XX9320 bill of INR 1211.9 is overdue. Pls make min payment of INR "
     "-100 to continue using your Credit Card. Ignore if paid - Axis Bank", "statement"),
    ("Your Axis Bank Internet Banking password is about to expire in 7 day(s). Please update your password.", "non_financial"),
])
def test_non_transaction_classes(conn, body, cls):
    row = add_sms(conn, body)
    out = process_sms_row(conn, row)
    assert out["class"] == cls, out
    assert txn_for(conn, row["id"]) is None
    assert sms_state(conn, row["id"]) == 1
    assert quarantine.summary(conn)["open"] == 0


# --- quarantine -----------------------------------------------------------------------------------

UNKNOWN = "Kotak Bank: INR 1,234.00 debited via SuperPay to WIDGET CO on 18-10-26 ref 77"


def test_unreadable_money_message_is_quarantined_not_dropped(conn):
    row = add_sms(conn, UNKNOWN, sender="AD-KOTAKB-S")
    out = process_sms_row(conn, row)
    assert out["class"] == "quarantined" and out["reason"] == "no_pattern"
    assert sms_state(conn, row["id"]) == 2
    s = quarantine.summary(conn)
    assert s["open"] == 1 and s["by_bank"] == {"Kotak": 1}
    # quarantined rows are not retried on every pending pass
    assert process_pending(conn)["total_pending"] == 0


def test_retry_quarantine_after_a_pattern_is_added(conn, make_card, monkeypatch):
    make_card("Axis", "9320", name="Airtel")
    row = add_sms(conn, AXIS_NEW, sender="AD-AXISBK-S")
    # what happened on 2026-10-16: the wording was unknown -> held
    monkeypatch.setattr(processing, "parse_single_sms", lambda body, reference_date=None: None)
    assert process_sms_row(conn, row)["class"] == "quarantined"
    monkeypatch.undo()
    other = add_sms(conn, UNKNOWN, sender="AD-KOTAKB-S")
    process_sms_row(conn, other)
    res = retry_quarantine(conn)
    assert res == {"retried": 2, "parsed": 1, "resolved": {"transaction": 1}, "still_open": 1}
    q = conn.execute("SELECT resolution, transaction_id FROM parse_quarantine WHERE source_ref=?", (str(row["id"]),)).fetchone()
    assert q["resolution"] == "transaction" and q["transaction_id"] == txn_for(conn, row["id"])["id"]
    assert sms_state(conn, row["id"]) == 1 and sms_state(conn, other["id"]) == 2


def test_a_crash_quarantines_instead_of_stalling_the_queue(conn, monkeypatch):
    row = add_sms(conn, AXIS_NEW, sender="AD-AXISBK-S")
    ok = add_sms(conn, HDFC_REFUND)

    real = processing.insert_transaction

    def flaky(c, **kw):
        if kw["source_ref"] == row["id"]:
            raise RuntimeError("disk full")
        return real(c, **kw)
    monkeypatch.setattr(processing, "insert_transaction", flaky)
    res = process_pending(conn)
    assert res["failed"] == 1 and res["processed"] == 1
    assert sms_state(conn, row["id"]) == 2 and sms_state(conn, ok["id"]) == 1
    assert "disk full" in conn.execute("SELECT reason FROM parse_quarantine").fetchone()["reason"]


# --- history --------------------------------------------------------------------------------------

def test_reprocess_history_dry_run_writes_nothing(conn, make_card):
    make_card("Axis", "9320", name="Airtel")
    make_card("HDFC", "9434", name="Swiggy")
    dropped = [add_sms(conn, AXIS_NEW, sender="AD-AXISBK-S", is_processed=1),
               add_sms(conn, HDFC_REFUND, is_processed=1),
               add_sms(conn, SWIGGY_NOTICE, sender="AD-SWIGGY-S", is_processed=1),
               add_sms(conn, UNKNOWN, sender="AD-KOTAKB-S", is_processed=1)]
    done = add_sms(conn, "Spent Rs.932 On HDFC Bank Card 9434 At RAZ*Swiggy On 2026-10-17:22:52:32.Not You?", is_processed=0)
    process_sms_row(conn, done)                               # has a transaction -> not history
    before = conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"]

    stats = reprocess_history(conn, dry_run=True)
    assert stats["dry_run"] is True and stats["examined"] == 4
    assert stats["by_class"] == {"transaction": 2, "noise": 1, "quarantined": 1}
    assert stats["transactions"]["count"] == 2
    assert stats["transactions"]["debit_total"] == 979.0 and stats["transactions"]["credit_total"] == 423.0
    assert conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"] == before
    assert [sms_state(conn, r["id"]) for r in dropped] == [1, 1, 1, 1]
    assert quarantine.summary(conn)["open"] == 0

    stats = reprocess_history(conn, dry_run=False)
    assert stats["by_class"] == {"transaction": 2, "noise": 1, "quarantined": 1}
    assert conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"] == before + 2
    assert sms_state(conn, dropped[3]["id"]) == 2
    # a second run finds nothing new to do
    assert reprocess_history(conn, dry_run=True)["examined"] == 1   # only the noise row remains txn-less
    assert reprocess_history(conn, since="2027-01-01")["examined"] == 0


def test_reprocess_history_flags_possible_twins(conn):
    # SBI sends a "spent" alert and an e-mandate debit alert for the first charge of a new mandate
    first = add_sms(conn, "Rs.399.00 spent on your SBI Credit Card ending 2111 at OPENAI *CHATGPT SUBSCR on 31/08/25. "
                          "Trxn. not done by you?", sender="VD-SBICRD-S", phone_timestamp="2025-08-31T10:11:00")
    process_sms_row(conn, first)
    add_sms(conn, "Transaction of Rs.399.00 at OPENAI *CHATGPT SUBSCR against E-mandate (SiHub ID - Fake0000Zz) "
                  "registered by you at merchant has been debited to your SBI Credit Card ending 2111 on 31-08-25.",
            sender="VD-SBICRD-S", phone_timestamp="2025-08-31T10:12:00", is_processed=1)
    stats = reprocess_history(conn)
    assert stats["by_class"] == {"transaction": 1} and stats["possible_twins"] == 1


def test_reprocess_history_picks_up_pluxee_fees_stored_as_non_financial(conn):
    # Before iter-18 the Pluxee fee SMS had one financial keyword and was stored is_financial = 0, so no
    # processing path ever looked at it. An OTP stored as non-financial stays untouched.
    fee = add_sms(conn, "Rs. 20.00 deducted from your Pluxee Card xxxx9113 towards PENDING INACTIVITY FEE FOR MAR. Pluxee",
                  sender="JD-Pluxee-S", phone_timestamp="2026-05-12T16:32:53", is_financial=0)
    otp = add_sms(conn, "123456 is your OTP. Do not share.", sender="AD-SWIGGY-S", is_financial=0)
    stats = reprocess_history(conn, dry_run=True)
    assert stats["reclassified_financial"] == 1 and stats["by_class"] == {"transaction": 1}
    assert conn.execute("SELECT is_financial FROM sms WHERE id=?", (fee["id"],)).fetchone()[0] == 0

    stats = reprocess_history(conn, dry_run=False)
    assert stats["transactions"]["by_kind"] == {"fee": 1}
    t = txn_for(conn, fee["id"])
    assert (t["amount"], t["date"], t["card_last4"]) == (20.0, "2026-05-12", "9113")
    assert [tuple(conn.execute("SELECT is_financial, is_processed FROM sms WHERE id=?", (i,)).fetchone())
            for i in (fee["id"], otp["id"])] == [(1, 1), (0, 0)]
    assert reprocess_history(conn)["examined"] == 0


# --- H5: retiring rows minted from merchant notices -----------------------------------------------

def test_retire_merchant_refund_notice_rows(conn):
    from modules.txn_store import insert_transaction
    notice = insert_transaction(conn, amount=423.0, date="2026-09-14", source="sms", source_ref="901",
                                raw_description="Swiggy", bank="Unknown", txn_type="credit", raw_text=SWIGGY_NOTICE)
    real = insert_transaction(conn, amount=423.0, date="2026-09-14", source="sms", source_ref="902",
                              raw_description="SWIGGY FOOD", bank="HDFC", card_last4="9434", txn_type="credit",
                              raw_text=HDFC_REFUND, pattern_kind="refund", context="cc")
    spend = insert_transaction(conn, amount=50.0, date="2026-09-14", source="sms", source_ref="903",
                               raw_description="X", txn_type="debit", raw_text="Your payment will be refunded")
    assert retire_merchant_refund_notice_rows(conn) == 1
    rows = {r["id"]: r for r in conn.execute("SELECT id, is_duplicate, merge_rule FROM transactions")}
    assert (rows[notice["id"]]["is_duplicate"], rows[notice["id"]]["merge_rule"]) == (1, "merchant-notice")
    assert rows[real["id"]]["is_duplicate"] == 0 and rows[spend["id"]]["is_duplicate"] == 0
    assert retire_merchant_refund_notice_rows(conn) == 0           # idempotent
    assert conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"] == 3   # nothing deleted


def test_classify_is_read_only(conn):
    row = add_sms(conn, AXIS_NEW, sender="AD-AXISBK-S")
    out = classify_sms_row(conn, row)
    assert out["class"] == "transaction" and out["txn"]["date"] == "2026-10-19"
    assert sms_state(conn, row["id"]) == 0 and txn_for(conn, row["id"]) is None


def test_stored_sms_rows_get_their_html_entities_decoded(conn):
    from modules.sms.processing import decode_html_entities
    from modules.txn_store import insert_transaction
    t = insert_transaction(conn, amount=2300, date="2025-08-27", source="sms", source_ref="e1",
                           raw_description="+2ND CD AT MAIN RD&#39;S C", card_last4="3605", context="atm")["id"]
    conn.execute("UPDATE transactions SET raw_description = '+2ND CD AT MAIN RD&#39;S C', merchant = 'AT MAIN RD&#39;S C' WHERE id = ?", (t,))
    assert decode_html_entities(conn, dry_run=True)["rows"] == 1
    decode_html_entities(conn, dry_run=False)
    r = conn.execute("SELECT raw_description, merchant FROM transactions WHERE id = ?", (t,)).fetchone()
    assert (r["raw_description"], r["merchant"]) == ("+2ND CD AT MAIN RD'S C", "AT MAIN RD'S C")
    assert decode_html_entities(conn, dry_run=True)["rows"] == 0
