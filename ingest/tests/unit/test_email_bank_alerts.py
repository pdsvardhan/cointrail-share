"""Bank transaction-alert emails as a transaction source — iter-18 H4.

Wordings are the real alert templates (scratchpad samples.txt / live emails, read-only); people's
names are replaced."""
import itertools
from datetime import date

import pytest

from modules.email import bank_alerts as ba
from modules.txn_store import insert_transaction

_gid = itertools.count(1)

SBI_FOOTER = (" Trxn. not done by you? Report at https://sbicard.com/Dispute. If you have not authorized this "
              "transaction please contact the SBI Card Helpline. Safe Banking Tip Never share your Card Number, "
              "CVV, PIN, OTP, Internet Banking User ID, Password or URN with anyone, even if the caller claims "
              "to be a bank employee.")


def sbi_spend(amount, merchant, d, card="2111"):
    return ("Untitled Document Dear Cardholder, This is to inform you that, "
            f"Rs.{amount} spent on your SBI Credit Card ending {card} at {merchant} on {d}." + SBI_FOOTER)


def add_email(conn, sender, subject, body, date_header):
    cur = conn.execute(
        "INSERT INTO emails (gmail_id, sender, sender_email, subject, body_snippet, date, is_financial) "
        "VALUES (?, ?, ?, ?, ?, ?, 1)",
        (f"g{next(_gid)}", sender, sender, subject, body, date_header),
    )
    conn.commit()
    return cur.lastrowid


def email_txn(conn, email_id):
    return conn.execute(
        "SELECT * FROM transactions WHERE source = 'email' AND source_ref = ?", (str(email_id),)
    ).fetchone()


def run(conn):
    return ba.process_bank_alert_emails(conn, since="2025-01-01")


# --- parse_alert_email: every live template ---------------------------------------------------

def test_sbi_card_spend():
    p = ba.parse_alert_email("onlinesbicard@sbicard.com", "Transaction Alert from CASHBACK SBI Card",
                             sbi_spend("287.00", "BLINKIT", "19/10/26"), "Mon, 19 Oct 2026 16:43:36 +0000")
    assert p["amount"] == 287.0 and p["currency"] == "INR" and p["card_last4"] == "2111"
    assert p["merchant"] == "BLINKIT" and p["date"] == "2026-10-19"
    assert p["txn_type"] == "debit" and p["context"] == "email-card" and p["bank"] == "SBI"


def test_sbi_aurum_variant():
    body = ("Untitled Document Dear AURUM Cardholder, Rs.81.42 spent on your SBI Credit Card ending 2971 at "
            "BIGTREEENTERTAINMENTPR on 18/10/26. Trxn. not done by you? Report at https://sbicard.com/Dispute")
    p = ba.parse_alert_email("onlinesbicard@sbicard.com", "Transaction Alert from AURUM SBI Card", body,
                             "Sun, 18 Oct 2026 04:12:28 +0000")
    assert (p["amount"], p["card_last4"], p["merchant"], p["date"]) == (81.42, "2971", "BIGTREEENTERTAINMENTPR",
                                                                        "2026-10-18")


def test_sbi_emandate_debit_success():
    body = ("Untitled Document Dear Cardholder, Thank you for registering for a recurring E-mandate at merchant "
            "platform using your SBI Credit Card. Transaction of Rs.500.00 at CANVA* I00000-0000001 against "
            "E-mandate (SiHub ID - YYqzzzz018) registered by you at merchant has been debited to your SBI Credit "
            "Card ending 2111 on 18-09-26. Click here , to manage your E-mandate Warm Regards SBI Card")
    p = ba.parse_alert_email("onlinesbicard@sbicard.com", "Recurring E-mandate debit success on your SBI Credit Card",
                             body, "Fri, 18 Sep 2026 05:56:30 +0000")
    assert (p["amount"], p["card_last4"], p["merchant"], p["date"]) == (500.0, "2111", "CANVA* I00000-0000001",
                                                                        "2026-09-18")


def test_sbi_emandate_processed_with_ref_and_time():
    body = ("Untitled Document Dear Cardholder, We're happy to inform you that your Recurring E-mandate registered "
            "at merchant site has been successfully processed on your SBI Credit Card XX ending with 2111. Please "
            "find below details of the transaction: Amount: Rs.310.00 Merchant: RAZ*Trendlynecom SiHub ID: "
            "Yqzzzzz002 Trxn. Ref. No.: 600000000000005 Date: 07-Oct-2026 Time: 02:42:24 To manage e- mandates")
    p = ba.parse_alert_email("onlinesbicard@sbicard.com",
                             "Recurring E-mandate processed successfully on your SBI Credit Card", body,
                             "Wed, 7 Oct 2026 07:12:25 +0000")
    assert (p["amount"], p["merchant"], p["ref_no"], p["date"]) == (310.0, "RAZ*Trendlynecom", "600000000000005",
                                                                    "2026-10-07")
    assert p["occurred_at"] == "2026-10-07T02:42:24+05:30"


def test_hdfc_credit_card_formats():
    a = ba.parse_alert_email(
        "alerts@hdfcbank.bank.in", "A payment was made using your Credit Card",
        "HDFC BANK --> Dear Customer, Greetings from HDFC Bank. We would like to inform you that Rs. 932.00 has "
        "been debited from your HDFC Bank Credit Card ending 9434 towards RAZ*Swiggy on 17 Oct, 2026 at "
        "22:52:33 . To check your available balance", "Sun, 18 Oct 2026 03:10:50 +0530")
    assert (a["amount"], a["card_last4"], a["merchant"], a["date"], a["occurred_at"]) == (
        932.0, "9434", "RAZ*Swiggy", "2026-10-17", "2026-10-17T22:52:33+05:30")
    b = ba.parse_alert_email(
        "alerts@hdfcbank.net", "Rs.1457.00 debited via Credit Card **9434",
        "HDFC BANK --> Dear Customer, Greetings from HDFC Bank! Rs.1457.00 is debited from your HDFC Bank Credit "
        "Card ending 9434 towards PYU*Swiggy Food on 09 Apr, 2026 at 14:36:26. To know your available balance",
        "Thu, 09 Apr 2026 14:36:30 +0530")
    assert (b["amount"], b["merchant"], b["date"]) == (1457.0, "PYU*Swiggy Food", "2026-04-09")
    c = ba.parse_alert_email(
        "alerts@hdfcbank.bank.in", "We noticed a transaction on your Credit Card",
        "HDFC BANK --> Dear Customer, Greetings from HDFC Bank. Thank you for using your HDFC Bank Credit Card "
        "ending in 9434 .You made a transaction of Rs. 435.00 at RAZ*Swiggy on 12-07-2026 13:50:58 . "
        "Authorization code: 000123 Important Note:", "Sun, 12 Jul 2026 13:51:03 +0530")
    assert (c["amount"], c["merchant"], c["ref_no"], c["date"]) == (435.0, "RAZ*Swiggy", "000123", "2026-07-12")


def test_hdfc_upi_debits_take_the_payee_name_and_reference():
    a = ba.parse_alert_email(
        "alerts@hdfcbank.bank.in", "You have done a UPI txn. Check details!",
        "HDFC BANK --> Dear Customer, Greetings from HDFC Bank! Rs.98.00 is debited from your account ending "
        "6611 towards VPA *************0616@yapl (VIJETHA SUPER MARKETS PVT LTD) on 17-10-26. UPI transaction "
        "reference no.: 600000000027. If you did not authorize", "Sat, 17 Oct 2026 21:42:16 +0530")
    assert (a["amount"], a["card_last4"], a["merchant"], a["ref_no"], a["context"]) == (
        98.0, "6611", "VIJETHA SUPER MARKETS PVT LTD", "600000000027", "upi")
    b = ba.parse_alert_email(
        "alerts@hdfcbank.net", "You have done a UPI txn. Check details!",
        "HDFC BANK --> Dear Customer, Rs.250.00 has been debited from account 6611 to VPA 9000000001@ibl "
        "Ravi Kumar on 09-04-26. Your UPI transaction reference number is 600000000018. If you did not",
        "Thu, 09 Apr 2026 20:08:39 +0530")
    assert (b["amount"], b["merchant"], b["ref_no"], b["date"]) == (250.0, "Ravi Kumar", "600000000018", "2026-04-09")
    rupay = ba.parse_alert_email(
        "alerts@hdfcbank.bank.in", "You have done a UPI txn. Check details!",
        "Dear Customer, Rs.213.00 has been debited from your HDFC Bank RuPay Credit Card XX4956 to "
        "paytm-blinkit@ptybl Blinkit on 01-02-26. Your UPI transaction reference number is 600000000011.",
        "Sun, 01 Feb 2026 09:21:24 +0530")
    assert (rupay["card_last4"], rupay["merchant"], rupay["context"]) == ("4956", "Blinkit", "email-card")


def test_hdfc_credits_are_credits():
    a = ba.parse_alert_email(
        "alerts@hdfcbank.bank.in", "View: Account update for your HDFC Bank A/c",
        "HDFC BANK --> Dear Customer, Greetings from HDFC Bank! We're writing to inform you that Rs.10000.00 "
        "has been successfully credited to your HDFC Bank account ending in 6611. Transaction Details: a. Date: "
        "09-10-26 b. Sender: ASHA RANI (VPA: 9000000002@axl) c. UPI Reference No.: 600000000019 Need Help?",
        "Fri, 9 Oct 2026 15:42:14 +0000")
    assert (a["amount"], a["txn_type"], a["merchant"], a["ref_no"], a["date"]) == (
        10000.0, "credit", "ASHA RANI", "600000000019", "2026-10-09")
    b = ba.parse_alert_email(
        "alerts@hdfcbank.net", "View: Account update for your HDFC Bank A/c",
        "HDFC BANK --> Dear Customer, Rs. 6700.00 is successfully credited to your account **6611 by VPA "
        "someone-3@okaxis RAVI KUMAR on 03-09-25. Your UPI transaction reference number is 600000000021.",
        "Wed, 03 Sep 2025 22:09:53 +0530")
    assert (b["amount"], b["txn_type"], b["merchant"], b["context"]) == (6700.0, "credit", "RAVI KUMAR", "upi")


def test_icici_spend_and_reversal():
    a = ba.parse_alert_email(
        "credit_cards@icicibank.com", "Transaction alert for your ICICI Bank Credit Card",
        "ICICI Bank Online Dear Customer, Your ICICI Bank Credit Card XX0026 has been used for a transaction of "
        "INR 509.13 on Apr 09, 2026 at 10:47:14. Info: K V KRISHNA RAO AAGFK1. The Available Credit Limit on "
        "your card is INR 2,96,324.36", "Thu, 9 Apr 2026 10:47:21 +0530")
    assert (a["amount"], a["card_last4"], a["merchant"], a["date"]) == (509.13, "0026", "K V KRISHNA RAO AAGFK1",
                                                                        "2026-04-09")
    assert ba.parse_alert_email(
        "credit_cards@icici.bank.in", "Transaction alert for your ICICI Bank Credit Card",
        "ICICI Bank Online Dear Customer, Your ICICI Bank Credit Card XX0026 has been used for a transaction of "
        "INR 1,080.00 on Oct 11, 2026 at 01:12:26. Info: K S R HANDLOOMS PRIVAT. The Available Credit Limit",
        "Sun, 11 Oct 2026 00:43:04 -0700 (PDT)")["amount"] == 1080.0
    r = ba.parse_alert_email(
        "credit_cards@icicibank.com", "Reversal processed on your ICICI Bank Credit Card XX0026",
        "Dear Customer, Apr 03, 2026 &nbsp; Greetings from ICICI Bank. We wish to inform you that the reversal "
        "of INR 4.01 has been done on your ICICI Bank Credit Card XX0026 on April 03, 2026. For any",
        "Fri, 3 Apr 2026 21:57:14 +0530")
    assert (r["amount"], r["txn_type"], r["pattern_kind"], r["date"]) == (4.01, "credit", "reversal", "2026-04-03")


def test_axis_spend_including_a_body_cut_to_the_snippet():
    a = ba.parse_alert_email(
        "alerts@axis.bank.in", "INR 979 spent on credit card no. XX9320",
        " 19-10-2026 Dear Customer, Here's the summary of your Axis Bank Credit Card Transaction: Transaction "
        "Amount: INR 979 Merchant Name: AIRTEL PAYM Axis Bank Credit Card No. XX9320 Date & Time: 19-10-2026, "
        "01:49:17 IST Available Limit*: INR 157920.62 ", "Mon, 19 Oct 2026 01:49:19 +0530")
    assert (a["amount"], a["card_last4"], a["merchant"], a["occurred_at"]) == (
        979.0, "9320", "AIRTEL PAYM", "2026-10-19T01:49:17+05:30")
    cut = ba.parse_alert_email(
        "alerts@axis.bank.in", "INR 548 spent on credit card no. XX9320",
        "CLICK HERE 20-12-2025 Dear Customer, Here&#39;s the summary of your Axis Bank Credit Card Transaction: "
        "Transaction Amount: INR 548 Merchant Name: AIRTEL PAYM Axis Bank Credit Card No.",
        "Sat, 20 Dec 2025 12:04:01 +0530")
    assert (cut["amount"], cut["card_last4"], cut["date"]) == (548.0, "9320", "2025-12-20")   # card from subject


def test_sbi_account_alerts():
    cbs = ba.parse_alert_email(
        "cbsalerts.sbi@alerts.sbi.bank.in", "CBSSBI ALERT",
        "Greetings from SBI ! Your A/C XXXXX004321 Debited INR 40,000.00 on 25/09/26 -Transferred to Mr. "
        "ANIL KUMAR. Avl Balance INR 81,754.23-SBI", "Fri, 25 Sep 2026 08:02:37 +0530")
    assert (cbs["amount"], cbs["card_last4"], cbs["merchant"], cbs["context"]) == (
        40000.0, "4321", "Transferred to Mr. ANIL KUMAR", "bank")
    rev = ba.parse_alert_email(
        "cbsalerts.sbi@alerts.sbi.bank.in", "CBSSBI ALERT",
        "Greetings from SBI ! Your AC XXXXX105555 Credited INR 12,000.00 on 05/10/26 -REVERSE ATM WDL. Avl Bal "
        "INR 23,111.11.-SBI", "Mon, 05 Oct 2026 10:26:46 +0530")
    assert (rev["txn_type"], rev["pattern_kind"], rev["card_last4"]) == ("credit", "reversal", "5555")
    atm = ba.parse_alert_email(
        "donotreply.sbiatm@alerts.sbi.co.in", "Transaction alert for your State Bank of India Debit Card",
        "Transaction Alert Message Oct 05, 2026, 11:15 Dear Valued SBI Debit Card Holder, The below transaction "
        "has been done using your SBI debit card. Description Value Bank SBI ATM ID S1BW600000099 Date &amp; "
        "Time Oct 05, 2026, 11:15 Transaction Number 1234 Amount (INR) 12000.00 Last 4 Digit of Account X5555 "
        "Transaction Type CASH WITHDRAWAL Channel ATM Available Balance (INR) 11111.11 Location ONSITE SBI "
        "TOWNSVILLE City TOWNSVILLE", "Mon, 05 Oct 2026 11:15:33 +0530")
    assert (atm["amount"], atm["card_last4"], atm["context"], atm["pattern_kind"], atm["date"]) == (
        12000.0, "5555", "atm", "atm", "2026-10-05")
    neft = ba.parse_alert_email(
        "neftinfo.itps@alerts.sbi.bank.in", "NEFT Transaction",
        "Dear Customer, Thank you for banking with State Bank of India. Your account has been credited for NEFT "
        "received as per the details given below Credited to Your A/c: XX4321 Amount: INR 1,50,000.00 UTR No.: "
        "HSBCN60000000029 Date: 12/10/2026 Sent by: MR ANIL KUMAR Sender Bank IFSC: HSBC0000001 ",
        "Mon, 12 Oct 2026 09:32:14 +0530")
    assert (neft["amount"], neft["txn_type"], neft["ref_no"], neft["merchant"]) == (
        150000.0, "credit", "HSBCN60000000029", "MR ANIL KUMAR")
    yono = ba.parse_alert_email(
        "yonobysbi@alerts.sbi.co.in", "Transaction success",
        "Dear Customer, SBI Ac X4321 debited for SI Fund Transfer of Rs.45000.00 to . Ac X0022 on 25.01.26 Ref "
        "600000032. Not done by you? call 1800111109. YONO, SBI", "Sun, 25 Jan 2026 11:17:26 +0530")
    assert (yono["amount"], yono["merchant"], yono["ref_no"], yono["date"]) == (
        45000.0, "SI FUND TRANSFER TO AC X0022", "600000032", "2026-01-25")


@pytest.mark.parametrize("sender,subject,body", [
    ("onlinesbicard@sbicard.com", "One Time Password (OTP) for your online transaction at BLINK COMM",
     "Dear Cardholder, The One Time Password (OTP) for your transaction at BLINK COMM of INR 287.00 with your "
     "SBI Credit Card ending 2111 is 390207. This OTP is valid for 10 minutes or 1 successful attempt"),
    ("alerts@hdfcbank.bank.in", "A payment was declined on your Credit Card due to card settings",
     "HDFC BANK --> Dear Customer, Greetings from HDFC Bank. We noticed that a transaction of Rs. 1934.00 on "
     "your HDFC Bank Credit Card ending 9434 was declined at RAZ*SWIGGY INSTAMART P . Reason: Domestic Online"),
    ("credit_cards@icici.bank.in", "Payment received on your ICICI Bank Credit Card.",
     "Dear Customer, Oct 07,2026 Greetings from ICICI Bank! We have received payment of INR 16,469.81 on your "
     "ICICI Bank Credit Card account 4315 XXXX XXXX 0026 on 07-Oct-2026. Looking forward"),
    ("emandates@sbicard.com", "Pre-Debit Alert: e-Mandate set at merchant using SBI Credit Card",
     "Dear Cardholder, Your payment of INR 310.00 at GISKARD DATATECH PRIVATE LIMITED is due on 07/10/2026 and "
     "will be processed through SBI Credit Card ending with 2111 as per e-Mandate (SiHub ID: Yqzzzzz002)"),
    ("credit_cards@icici.bank.in", "Amazon Pay ICICI Bank Credit Card Statement for the period August 21",
     "Credit Card Statement Payment due by October 8, 2026 ICICI Bank Credit Card XX0026 Minimum Amount Due: "
     "₹5,440.00 Total Amount Due: &#8377;16,469.81 Pay now using: iMobile Net Banking UPI"),
    ("offers@offers.sbicard.com", "Pay Your Credit Card Outstanding in Easy EMIs",
     "SBI Having trouble viewing this e-mail? Transfer your other bank's credit card outstanding balance"),
    ("yonobysbi@alerts.sbi.co.in", "Transaction failed",
     "Dear Customer, Failure! SI of Rs.45,000.00 from SBI Ac X4321 to . Ac X0022 has failed due to "
     "insufficient balance. YONO, SBI"),
])
def test_not_transactions(sender, subject, body):
    assert ba.parse_alert_email(sender, subject, body, "Mon, 19 Oct 2026 12:48:32 +0530") is None


def test_dates_are_ist_and_come_from_the_body_when_printed():
    # no date in the body: the IST day of the header (19:00 UTC on the 18th is 00:30 IST on the 19th)
    p = ba.parse_alert_email(
        "alerts@hdfcbank.bank.in", "View: Account update for your HDFC Bank A/c",
        "Dear Customer, Thank you for banking with HDFC Bank. Rs. 42000.00 has been deducted from your HDFC Bank "
        "account ending in XX6611 for a transfer to payee Anil Kumar via NEFT using HDFC Bank",
        "Sat, 18 Oct 2026 19:00:00 +0000")
    assert p["date"] == "2026-10-19"
    assert ba.header_ist_date("Mon, 19 Oct 2026 12:48:32 +0530 (IST)") == "2026-10-19"
    # the body date wins over a late email
    late = ba.parse_alert_email("onlinesbicard@sbicard.com", "Transaction Alert from CASHBACK SBI Card",
                                sbi_spend("287.00", "BLINKIT", "18/10/26"), "Mon, 19 Oct 2026 01:00:00 +0530")
    assert late["date"] == "2026-10-18"
    assert ba.parse_body_date("17 Oct, 2026") == date(2026, 10, 17)
    assert ba.parse_body_date("Apr 09, 2026") == date(2026, 4, 9)
    assert ba.parse_body_date("12.10.26") == date(2026, 10, 12)


# --- process_bank_alert_emails -----------------------------------------------------------------

def test_email_only_spend_becomes_a_live_transaction(conn, make_card):
    make_card("SBI", "2111", statement_day=19, due_day=8)
    eid = add_email(conn, "onlinesbicard@sbicard.com", "Transaction Alert from CASHBACK SBI Card",
                    sbi_spend("542.00", "BLINKIT", "17/10/26"), "Sat, 17 Oct 2026 14:00:00 +0000")
    stats = run(conn)
    assert stats["inserted"] == 1 and stats["linked_as_duplicate"] == 0
    t = email_txn(conn, eid)
    assert t["is_duplicate"] == 0 and t["amount"] == 542.0 and t["card_last4"] == "2111"
    assert t["date"] == "2026-10-17" and t["category"] == "Groceries" and t["kind"] == "purchase"


def test_alert_for_a_purchase_with_an_sms_is_a_duplicate(conn, make_card):
    make_card("SBI", "2111")
    sms = insert_transaction(conn, amount=287.0, date="2026-10-19", source="sms", source_ref="4060",
                             raw_description="BLINKIT", bank="SBI", card_last4="2111", context="cc")
    eid = add_email(conn, "onlinesbicard@sbicard.com", "Transaction Alert from CASHBACK SBI Card",
                    sbi_spend("287.00", "BLINKIT", "19/10/26"), "Mon, 19 Oct 2026 16:43:36 +0000")
    assert run(conn)["linked_as_duplicate"] == 1
    t = email_txn(conn, eid)
    assert t["is_duplicate"] == 1 and t["matched_txn_id"] == sms["id"]
    assert conn.execute("SELECT COUNT(*) c FROM transactions WHERE is_duplicate = 0").fetchone()["c"] == 1


def test_alert_for_a_purchase_on_the_statement_is_a_duplicate(conn, make_card):
    make_card("SBI", "2111", pdf_card_last4="9181")
    line = insert_transaction(conn, amount=449.40, date="2026-10-16", source="card", source_ref="stmt.pdf#3",
                              raw_description="CTRLX TECHNOLOGIES PRIV MUMBAI IN", bank="sbi", card_last4="9181",
                              context="card")
    eid = add_email(conn, "onlinesbicard@sbicard.com", "Transaction Alert from CASHBACK SBI Card",
                    sbi_spend("449.40", "CTRLXTECHNOLOGIESPRIV", "16/10/26"), "Fri, 16 Oct 2026 15:12:00 +0000")
    run(conn)
    t = email_txn(conn, eid)
    assert t["is_duplicate"] == 1 and t["matched_txn_id"] == line["id"]


def test_sms_arriving_after_the_alert_wins_and_the_alert_becomes_the_duplicate(conn, make_card):
    make_card("HDFC", "9434")
    eid = add_email(conn, "alerts@hdfcbank.bank.in", "A payment was made using your Credit Card",
                    "HDFC BANK --> Dear Customer, Rs. 932.00 has been debited from your HDFC Bank Credit Card ending "
                    "9434 towards RAZ*Swiggy on 17 Oct, 2026 at 22:52:33 . To check your available balance",
                    "Sun, 18 Oct 2026 03:10:50 +0530")
    run(conn)
    sms = insert_transaction(conn, amount=932.0, date="2026-10-17", source="sms", source_ref="4058",
                             raw_description="RAZ*Swiggy", bank="HDFC", card_last4="9434", context="cc")
    t = email_txn(conn, eid)
    assert t["is_duplicate"] == 1 and t["matched_txn_id"] == sms["id"]


def test_unreadable_transaction_alert_is_quarantined_not_dropped(conn, make_card):
    make_card("SBI", "2111")
    body = ("Dear Cardholder, A purchase of Rs.250.00 was made with your SBI Card XX2111 at ZEPTO on "
            "19 Oct 2026. Not you? Call the SBI Card helpline immediately.")
    eid = add_email(conn, "onlinesbicard@sbicard.com", "Transaction Alert from CASHBACK SBI Card", body,
                    "Mon, 19 Oct 2026 12:48:32 +0530")
    stats = run(conn)
    assert stats["quarantined"] == 1 and stats["inserted"] == 0
    q = conn.execute("SELECT * FROM parse_quarantine WHERE source = 'email' AND source_ref = ?",
                     (str(eid),)).fetchone()
    assert q["reason"] == "no_pattern" and q["bank"] == "SBI"
    assert q["excerpt"].startswith("Transaction Alert from CASHBACK SBI Card | Dear Cardholder")
    # hourly runs leave it held; it is not re-counted or turned into a guess
    assert run(conn)["quarantined"] == 0
    assert conn.execute("SELECT attempts FROM parse_quarantine").fetchone()["attempts"] == 1


def test_processing_is_idempotent(conn, make_card):
    make_card("SBI", "2111")
    add_email(conn, "onlinesbicard@sbicard.com", "Transaction Alert from CASHBACK SBI Card",
              sbi_spend("306.00", "BLINKIT", "18/10/26"), "Sun, 18 Oct 2026 10:00:00 +0000")
    add_email(conn, "onlinesbicard@sbicard.com", "One Time Password (OTP) for your online transaction at BLINK",
              "Dear Cardholder, The One Time Password (OTP) for your transaction at BLINK COMM of INR 306.00 with "
              "your SBI Credit Card ending 2111 is 390207.", "Sun, 18 Oct 2026 09:59:00 +0000")
    first = run(conn)
    assert (first["inserted"], first["skipped"]) == (1, 1)
    second = run(conn)
    assert second["inserted"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM transactions WHERE source = 'email'").fetchone()["c"] == 1


def test_two_identical_purchases_are_two_alerts_and_two_transactions(conn, make_card):
    make_card("SBI", "2111")
    for t in ("10:00:00", "10:05:00"):
        add_email(conn, "onlinesbicard@sbicard.com", "Transaction Alert from CASHBACK SBI Card",
                  sbi_spend("287.00", "BLINKIT", "19/10/26"), f"Mon, 19 Oct 2026 {t} +0000")
    run(conn)
    assert conn.execute("SELECT COUNT(*) c FROM transactions WHERE is_duplicate = 0").fetchone()["c"] == 2


def test_two_templates_for_one_emandate_debit_count_once(conn, make_card):
    """SBI sends a no-AFA 'Transaction Alert' AND an 'E-mandate debit success' for every e-mandate."""
    make_card("SBI", "2111")
    hdr = "Thu, 02 Oct 2025 00:17:09 +0530"
    add_email(conn, "onlinesbicard@sbicard.com", "Transaction Alert from CASHBACK SBI Card",
              "SBI Card TRANSACTION ALERT! Dear Cardholder, This is to inform you that, Trxn. of Rs.399.00 done on "
              "your credit card ending 2111 without effecting add&#39;nl factor of authentication mandated by RBI, "
              "at OPENAICHATGPTSUBSCR on 02 Oct 25 . If this Trxn. wasn't done by you, click "
              "https://sbicard.com/DisputeRaise - SBI Card." + SBI_FOOTER, hdr)
    add_email(conn, "onlinesbicard@sbicard.com", "Recurring E-mandate debit success on your SBI Credit Card",
              "Dear Cardholder, Thank you for registering for a recurring E-mandate at merchant platform using "
              "your SBI Credit Card. Transaction of Rs.399.00 at OPENAI *CHATGPT SUBSCR against E-mandate (SiHub "
              "ID - Xyz) registered by you at merchant has been debited to your SBI Credit Card ending 2111 on "
              "02-10-25. Click here , to manage your E-mandate Warm Regards SBI Card", hdr)
    stats = run(conn)
    assert stats["inserted"] == 2 and stats["linked_as_duplicate"] == 1
    live = conn.execute("SELECT * FROM transactions WHERE is_duplicate = 0").fetchall()
    assert len(live) == 1 and live[0]["amount"] == 399.0
    assert conn.execute("SELECT merge_rule FROM transactions WHERE is_duplicate = 1").fetchone()[0] == "same-alert"


def test_cbs_and_yono_alerts_for_one_transfer_count_once(conn, make_card):
    make_card("SBI", "4321", name="Savings", active=0)
    add_email(conn, "cbsalerts.sbi@alerts.sbi.bank.in", "CBSSBI ALERT",
              "Greetings from SBI ! Your A/C XXXXX004321 Debited INR 50,000.00 on 12/10/26 -Transferred to Mrs. "
              "ASHA RANI. Avl Balance INR 81,754.23-SBI Please do not reply to this auto generated email.",
              "Mon, 12 Oct 2026 10:17:20 +0530")
    add_email(conn, "yonobysbi@alerts.sbi.bank.in", "Transaction success",
              "Dear Customer Thank you for using YONO SBI for Fund Transfer The transaction details are as follows: "
              "Description Details Transaction Status Successful Amount Rs.50,000.00 Transaction Number 600000033 "
              "Date of Transaction 12.10.26 Debit account x4321 Beneficiary Name Asha Rani Beneficiary Account "
              "Number x7777", "Mon, 12 Oct 2026 10:17:36 +0530")
    run(conn)
    live = conn.execute("SELECT * FROM transactions WHERE is_duplicate = 0").fetchall()
    assert len(live) == 1 and live[0]["amount"] == 50000.0 and live[0]["card_last4"] == "4321"
    assert live[0]["raw_description"] == "Transferred to Mrs. ASHA RANI"          # the first alert stays live


def test_foreign_emandate_uses_the_statement_rate_and_its_amountless_twin_is_skipped(conn, make_card):
    make_card("SBI", "2111")
    insert_transaction(conn, amount=10916.12, date="2026-03-18", source="card", source_ref="mar.pdf#9",
                       raw_description="CLAUDE.AI SUBSCRIPTION ANTHROPIC.COM CA 118.00 USD", bank="sbi",
                       card_last4="2111", context="card")
    hdr = "Thu, 24 Sep 2026 23:50:39 +0000"
    alert = add_email(conn, "onlinesbicard@sbicard.com", "Transaction Alert from CASHBACK SBI Card",
                      "Untitled Document Dear Cardholder, This is to inform you that, Trxn. of USD.00 done on your "
                      "credit card ending 2111 without effecting add'nl factor of authentication mandated by RBI, at "
                      "ANTHROPICCLAUDESUB on 25 Sep 26 . If this Trxn. wasn't done by you, click "
                      "https://sbicard.com/DisputeRaise - SBI Card." + SBI_FOOTER, hdr)
    mandate = add_email(conn, "onlinesbicard@sbicard.com",
                        "Recurring E-mandate processed successfully on your SBI Credit Card",
                        "Untitled Document Dear Cardholder, We're happy to inform you that your Recurring E-mandate "
                        "registered at merchant site has been successfully processed on your SBI Credit Card XX "
                        "ending with 2111. Please find below details of the transaction: Amount: USD118.00 Merchant: "
                        "ANTHROPIC* CLAUDE SUB SiHub ID: Yqzzzzz003 Trxn. Ref. No.: 600000000000006 Date: "
                        "25-Sep-2026 Time: 19:20:38 To manage e- mandates, please Click here .", hdr)
    stats = run(conn)
    assert stats["skip_reasons"].get("twin_without_amount") == 1 and stats["quarantined"] == 0
    assert email_txn(conn, alert) is None
    t = email_txn(conn, mandate)
    assert (t["currency"], t["original_amount"], t["fx_estimated"]) == ("USD", 118.0, 1)
    assert t["amount"] == 10916.12 and t["category"] == "Subscriptions"


def test_foreign_alert_without_any_known_rate_is_quarantined(conn, make_card):
    make_card("ICICI", "8013")
    eid = add_email(conn, "credit_cards@icicibank.com", "Transaction alert for your ICICI Bank Credit Card",
                    "ICICI Bank Online Dear Customer, Your ICICI Bank Credit Card XX8013 has been used for a "
                    "transaction of USD 71.76 on Mar 03, 2026 at 03:57:38. Info: PROTONMAIL. The Available Credit "
                    "Limit on your card is INR 92,317.33", "Mon, 02 Mar 2026 14:28:01 -0800 (PST)")
    assert run(conn)["quarantined"] == 1
    assert conn.execute("SELECT reason FROM parse_quarantine WHERE source_ref = ?",
                        (str(eid),)).fetchone()["reason"] == "fx_rate_unknown"


def test_bank_account_alerts_need_a_declared_account(conn, make_card):
    body = ("HDFC BANK --> Dear Customer, Rs.250.00 has been debited from account 6611 to VPA 9000000001@ibl "
            "Ravi Kumar on 09-04-26. Your UPI transaction reference number is 600000000018. If you did not")
    eid = add_email(conn, "alerts@hdfcbank.net", "You have done a UPI txn. Check details!", body,
                    "Thu, 09 Apr 2026 20:08:39 +0530")
    stats = run(conn)
    assert stats["skip_reasons"] == {"unknown_account": 1} and stats["unknown_accounts"] == {"6611": 1}
    assert email_txn(conn, eid) is None
    make_card("HDFC", "6611", name="Account", active=0)          # owner declares the account
    assert run(conn)["inserted"] == 1
    t = email_txn(conn, eid)
    assert (t["ref_no"], t["kind"], t["category"]) == ("600000000018", "debit", "Transfer")


def test_truncated_old_alert_is_skipped_not_quarantined(conn, make_card):
    make_card("SBI", "5555", active=0)
    add_email(conn, "donotreply.sbiatm@alerts.sbi.co.in", "Transaction alert for your State Bank of India Debit Card",
              "Transaction Alert Message Feb 12, 2026, 08:35 Dear Valued SBI Debit Card Holder, The below "
              "transaction has been done using your SBI debit card. Description Value Bank SBI ATM ID T1BH600000030 "
              "Date", "Thu, 12 Feb 2026 08:35:10 +0530")
    stats = run(conn)
    assert stats["skip_reasons"] == {"truncated_body": 1} and stats["quarantined"] == 0


def test_hourly_default_window_is_recent_ist_days(conn, make_card, monkeypatch):
    make_card("SBI", "2111")
    monkeypatch.setattr(ba, "today_ist", lambda: date(2026, 10, 20))
    add_email(conn, "onlinesbicard@sbicard.com", "Transaction Alert from CASHBACK SBI Card",
              sbi_spend("287.00", "BLINKIT", "19/10/26"), "Mon, 19 Oct 2026 16:43:36 +0000")
    add_email(conn, "onlinesbicard@sbicard.com", "Transaction Alert from CASHBACK SBI Card",
              sbi_spend("215.00", "ZEPTOMARKETPLACEPRIVATE", "12/12/25"), "Fri, 12 Dec 2025 10:00:00 +0000")
    stats = ba.process_bank_alert_emails(conn)
    assert stats["scanned"] == 1 and stats["inserted"] == 1


def test_backfill_dry_run_writes_nothing_and_reports_per_card(conn, make_card):
    make_card("SBI", "2111")
    insert_transaction(conn, amount=287.0, date="2026-10-19", source="sms", source_ref="1",
                       raw_description="BLINKIT", card_last4="2111", context="cc")
    add_email(conn, "onlinesbicard@sbicard.com", "Transaction Alert from CASHBACK SBI Card",
              sbi_spend("287.00", "BLINKIT", "19/10/26"), "Mon, 19 Oct 2026 16:43:36 +0000")
    add_email(conn, "onlinesbicard@sbicard.com", "Transaction Alert from CASHBACK SBI Card",
              sbi_spend("542.00", "BLINKIT", "17/10/26"), "Sat, 17 Oct 2026 14:00:00 +0000")
    add_email(conn, "onlinesbicard@sbicard.com", "Transaction Alert from CASHBACK SBI Card",
              "Dear Cardholder, A purchase of Rs.99.00 was made with your SBI Card XX2111 at ZEPTO.",
              "Sat, 17 Oct 2026 15:00:00 +0000")
    before = conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"]
    dry = ba.backfill_alert_emails(conn, since="2026-10-01", dry_run=True)
    assert dry["dry_run"] is True and dry["inserted"] == 2 and dry["quarantined"] == 1
    assert dry["by_card"]["2111"] == {"new": 1, "new_amount": 542.0, "linked": 1, "linked_amount": 287.0,
                                      "by_rule": {"unique": 1}}
    assert conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"] == before
    assert conn.execute("SELECT COUNT(*) c FROM parse_quarantine").fetchone()["c"] == 0
    real = ba.backfill_alert_emails(conn, since="2026-10-01", dry_run=False)
    assert real["by_card"] == dry["by_card"]
    assert conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"] == before + 2


def test_backfill_retries_quarantine_and_resolves_it(conn, make_card, monkeypatch):
    make_card("SBI", "2111")
    eid = add_email(conn, "onlinesbicard@sbicard.com", "Transaction Alert from CASHBACK SBI Card",
                    sbi_spend("287.00", "BLINKIT", "19/10/26"), "Mon, 19 Oct 2026 16:43:36 +0000")
    real_recognise = ba._recognise
    monkeypatch.setattr(ba, "_recognise", lambda *a: ("unknown", None))      # a wording we cannot read yet
    assert run(conn)["quarantined"] == 1
    monkeypatch.setattr(ba, "_recognise", real_recognise)                    # pattern shipped
    assert run(conn)["inserted"] == 0                                        # hourly leaves it held
    assert ba.backfill_alert_emails(conn, since="2026-10-01", dry_run=False)["inserted"] == 1
    q = conn.execute("SELECT resolution, transaction_id FROM parse_quarantine").fetchone()
    assert q["resolution"] == "parsed" and q["transaction_id"] == email_txn(conn, eid)["id"]


def _cbs_mail(bal, when):
    return ("cbsalerts.sbi@alerts.sbi.bank.in", "CBSSBI ALERT",
            "Greetings from SBI ! Your A/C XXXXX004321 Debited INR 50,000.00 on 12/10/26 -Transferred to Mrs. "
            f"ASHA RANI. Avl Balance INR {bal}-SBI Please do not reply to this auto generated email.", when)


def _yono_mail(txn_no, when):
    return ("yonobysbi@alerts.sbi.bank.in", "Transaction success",
            "Dear Customer Thank you for using YONO SBI for Fund Transfer The transaction details are as follows: "
            f"Description Details Transaction Status Successful Amount Rs.50,000.00 Transaction Number {txn_no} "
            "Date of Transaction 12.10.26 Debit account x4321 Beneficiary Name Asha Rani Beneficiary Account "
            "Number x7777", when)


@pytest.mark.parametrize("second", [
    [_yono_mail("600000044", "Mon, 12 Oct 2026 13:00:10 +0530")],                  # only YONO saw it
    [_cbs_mail("61,111.11", "Mon, 12 Oct 2026 13:00:00 +0530")],                  # only core banking saw it
    [_cbs_mail("61,111.11", "Mon, 12 Oct 2026 13:00:00 +0530"), _yono_mail("600000044", "Mon, 12 Oct 2026 13:00:10 +0530")],
])
def test_a_second_same_day_transfer_seen_only_by_email_stays_a_second_transfer(conn, make_card, second):
    make_card("SBI", "4321", name="Savings", active=0)
    add_email(conn, *_cbs_mail("1,11,111.11", "Mon, 12 Oct 2026 10:17:20 +0530"))
    add_email(conn, *_yono_mail("600000043", "Mon, 12 Oct 2026 10:17:36 +0530"))
    for mail in second:
        add_email(conn, *mail)
    run(conn)
    live = conn.execute("SELECT * FROM transactions WHERE is_duplicate = 0").fetchall()
    assert len(live) == 2


def _yono_sms(conn, sid, ref, ts):
    from modules.txn_store import insert_transaction
    body = f"Your Ac Xx4321 debited Rs.50,000.00 for transfer to Asha Ac Xx7777 dt 12.10.26 Ref {ref}. YONO SBI"
    conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, is_financial, is_processed) VALUES (?, ?, ?, ?, 1, 1)",
                 (sid, "AD-SBYONO-S", body, ts))
    insert_transaction(conn, amount=50000, date="2026-10-12", source="sms", source_ref=str(sid), raw_description="Asha",
                       card_last4="4321", raw_text=body, ref_no=ref, context="bank")


def _cbs_sms(conn, sid, bal, ts):
    from modules.txn_store import insert_transaction
    body = f"Your A/C XXXXX004321 Debited INR 50,000.00 on 12/10/26 -Transferred to Mrs. ASHA RANI. Avl Balance INR {bal}-SBI"
    conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, is_financial, is_processed) VALUES (?, ?, ?, ?, 1, 1)",
                 (sid, "AD-CBSSBI-S", body, ts))
    insert_transaction(conn, amount=50000, date="2026-10-12", source="sms", source_ref=str(sid),
                       raw_description="Mrs. ASHA RANI", card_last4="4321", raw_text=body, context="bank")


def test_second_transfer_seen_only_by_its_two_emails_beside_a_first_with_an_sms(conn, make_card):
    """Transfer 1: YONO SMS + both alert emails. Transfer 2 (no SMS): its two emails, stored newest
    first. Two transfers -> two live rows (it used to end as 3)."""
    from modules.merge import run_merge_sweep
    make_card("SBI", "4321", name="Savings", active=0)
    _yono_sms(conn, 900, "600000043", "2026-10-12T04:47:30")
    add_email(conn, *_cbs_mail("1,11,111.11", "Mon, 12 Oct 2026 10:17:20 +0530"))
    add_email(conn, *_yono_mail("600000043", "Mon, 12 Oct 2026 10:17:36 +0530"))
    run(conn)
    add_email(conn, *_yono_mail("600000044", "Mon, 12 Oct 2026 12:30:10 +0530"))
    add_email(conn, *_cbs_mail("61,111.11", "Mon, 12 Oct 2026 12:30:00 +0530"))
    run(conn)
    run_merge_sweep(conn)
    assert conn.execute("SELECT COUNT(*) c FROM transactions WHERE is_duplicate = 0").fetchone()["c"] == 2


def test_second_transfer_seen_only_by_core_banking_email_is_not_absorbed(conn, make_card):
    """Transfer 1's group holds a core-banking SMS (balance 1,11,111.11); transfer 2 is seen only by
    its core-banking email (balance 61,111.11)."""
    from modules.merge import run_merge_sweep
    make_card("SBI", "4321", name="Savings", active=0)
    _yono_sms(conn, 910, "600000053", "2026-10-12T04:47:30")
    _cbs_sms(conn, 911, "1,11,111.11", "2026-10-12T04:47:50")
    add_email(conn, *_cbs_mail("61,111.11", "Mon, 12 Oct 2026 12:30:00 +0530"))
    run(conn)
    run_merge_sweep(conn)
    assert conn.execute("SELECT COUNT(*) c FROM transactions WHERE is_duplicate = 0").fetchone()["c"] == 2


def test_one_transfer_with_sms_and_both_emails_is_one_row(conn, make_card):
    from modules.merge import run_merge_sweep
    make_card("SBI", "4321", name="Savings", active=0)
    _yono_sms(conn, 920, "600000063", "2026-10-12T04:47:30")
    _cbs_sms(conn, 921, "1,11,111.11", "2026-10-12T04:47:50")
    add_email(conn, *_yono_mail("600000063", "Mon, 12 Oct 2026 10:17:36 +0530"))
    add_email(conn, *_cbs_mail("1,11,111.11", "Mon, 12 Oct 2026 10:17:20 +0530"))
    run(conn)
    run_merge_sweep(conn)
    assert conn.execute("SELECT COUNT(*) c FROM transactions WHERE is_duplicate = 0").fetchone()["c"] == 1


# --- two same-amount transfers 30 minutes apart, their alert emails in one newest-first batch ------

def _ye(n, t):
    return _yono_mail(n, f"Mon, 12 Oct 2026 {t} +0530")


def _ce(bal, t):
    return _cbs_mail(bal, f"Mon, 12 Oct 2026 {t} +0530")


def _live(conn):
    return conn.execute("SELECT COUNT(*) c FROM transactions WHERE is_duplicate = 0").fetchone()["c"]


def test_f1_second_transfer_seen_only_by_core_banking_email_in_the_same_batch(conn, make_card):
    from modules.merge import run_merge_sweep
    make_card("SBI", "4321", name="Savings", active=0)
    _yono_sms(conn, 930, "600000073", "2026-10-12T04:47:30")
    _cbs_sms(conn, 931, "1,11,111.11", "2026-10-12T04:47:50")
    for mail in (_ce("61,111.11", "10:47:20"), _ce("1,11,111.11", "10:17:20"), _ye("600000073", "10:17:36")):
        add_email(conn, *mail)
    run(conn)
    run_merge_sweep(conn)
    assert _live(conn) == 2


def test_f2_two_fully_alerted_transfers_in_one_batch(conn, make_card):
    from modules.merge import run_merge_sweep
    make_card("SBI", "4321", name="Savings", active=0)
    _yono_sms(conn, 940, "600000081", "2026-10-12T04:47:30")
    _yono_sms(conn, 941, "600000082", "2026-10-12T05:17:30")
    _cbs_sms(conn, 942, "61,111.11", "2026-10-12T05:17:50")
    for mail in (_ce("61,111.11", "10:47:56"), _ye("600000082", "10:47:36"), _ce("1,11,111.11", "10:17:56"),
                 _ye("600000081", "10:17:36")):
        add_email(conn, *mail)
    run(conn)
    run_merge_sweep(conn)
    assert _live(conn) == 2


def test_f3_core_banking_first_then_a_yono_transfer_30_minutes_later(conn, make_card):
    from modules.merge import run_merge_sweep
    make_card("SBI", "4321", name="Savings", active=0)
    _cbs_sms(conn, 950, "1,11,111.11", "2026-10-12T04:48:00")
    _yono_sms(conn, 951, "600000092", "2026-10-12T05:17:32")
    for mail in (_ce("61,111.11", "10:47:56"), _ye("600000092", "10:47:36"), _ce("1,11,111.11", "10:18:20")):
        add_email(conn, *mail)
    run(conn)
    run_merge_sweep(conn)
    assert _live(conn) == 2


def test_two_purchases_35_seconds_apart_keep_their_own_sms_and_email(conn, make_card):
    """Card 9434, two Rs 4,259 purchases at 22:36:22 and 22:36:57: each statement line must hold the
    SMS and the email with ITS time (the 60 s slack crossed them)."""
    from modules.merge import run_merge_sweep
    make_card("HDFC", "9434")
    for sid, t in ((960, "22:36:22"), (961, "22:36:57")):
        body = f"Spent Rs.4259 On HDFC Bank Card 9434 At SAMPLE STORE On 2025-11-23:{t}.Not You?"
        conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, is_financial, is_processed) VALUES (?, ?, ?, ?, 1, 1)",
                     (sid, "JX-HDFCBK-S", body, f"2025-11-23T17:0{sid - 960}:00"))
        insert_transaction(conn, amount=4259, date="2025-11-23", source="sms", source_ref=str(sid), raw_description="SAMPLE STORE",
                           card_last4="9434", raw_text=body, context="cc")
    for t, hdr in (("22:36:57", "Sun, 23 Nov 2025 22:36:58 +0530"), ("22:36:23", "Sun, 23 Nov 2025 22:36:24 +0530")):
        add_email(conn, "alerts@hdfcbank.bank.in", "A payment was made using your Credit Card",
                  "HDFC BANK --> Dear Customer, Greetings from HDFC Bank. We would like to inform you that Rs. 4259.00 has "
                  f"been debited from your HDFC Bank Credit Card ending 9434 towards SAMPLE STORE on 23 Nov, 2025 at {t} . "
                  "To check your available balance", hdr)
    run(conn)
    for n in (42, 43):
        insert_transaction(conn, amount=4259, date="2025-11-23", source="card", source_ref=f"st#{n}",
                           raw_description="EMI PPSL*SWIGGYBANGALORE", card_last4="9434", context="card")
    run_merge_sweep(conn)
    from modules.merge import _alert_text, _body_facts, _seconds
    groups = {}
    for r in conn.execute("SELECT * FROM transactions WHERE is_duplicate = 1"):
        groups.setdefault(r["matched_txn_id"], []).append(_seconds(_body_facts(_alert_text(conn, r))["time"]))
    assert _live(conn) == 2 and len(groups) == 2
    assert all(max(v) - min(v) <= 5 for v in groups.values())      # an SMS and ITS email (1 s apart), never 35 s



def test_repro_a_mails_first_then_both_transfers_sms(conn, make_card):
    from modules.merge import run_merge_sweep
    make_card("SBI", "4321", name="Savings", active=0)
    for mail in (_ye("600000102", "10:47:36"), _ce("1,11,111.11", "10:17:56")):
        add_email(conn, *mail)
    run(conn)
    _yono_sms(conn, 970, "600000101", "2026-10-12T04:47:32")
    _cbs_sms(conn, 971, "61,111.11", "2026-10-12T05:18:00")
    run(conn)
    run_merge_sweep(conn)
    assert _live(conn) == 2


def test_repro_b_two_yono_transfers_90_seconds_apart(conn, make_card):
    from modules.merge import run_merge_sweep
    make_card("SBI", "4321", name="Savings", active=0)
    _yono_sms(conn, 980, "600000111", "2026-10-12T04:47:32")
    _yono_sms(conn, 981, "600000112", "2026-10-12T04:49:02")
    _cbs_sms(conn, 982, "61,111.11", "2026-10-12T04:49:30")
    for mail in (_ce("61,111.11", "10:19:26"), _ce("1,11,111.11", "10:17:56")):
        add_email(conn, *mail)
    run(conn)
    run_merge_sweep(conn)
    assert _live(conn) == 2


def test_standing_instruction_seen_by_yono_and_core_banking_sms_and_mails_is_one_transfer(conn, make_card):
    """YONO names the loan account ('to . Ac X0001'), core banking names the owner: the texts never
    agree, but the two SMS are 36 s apart on one account for one amount."""
    from modules.merge import run_merge_sweep
    make_card("SBI", "4321", name="Savings", active=0)
    yono = ("SBI Ac X4321 debited for SI Fund Transfer of Rs.45,000.00 to . Ac X0001 on 24.02.26 Ref 300000000301. "
            "Not done by you? Call 1800111109.YONO, SBI")
    cbs = ("Your A/C XXXXX004321 Debited INR 45,000.00 on 24/02/26 -Transferred to Mr. SAM. Avl Balance INR 99,219.17-SBI")
    for sid, sender, body, ts, desc, ref in ((990, "AX-SBYONO-S", yono, "2026-02-24T10:26:44", "Ac X0001", "300000000301"),
                                            (991, "AD-CBSSBI-S", cbs, "2026-02-24T10:27:20", "Mr. SAM", None)):
        conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, is_financial, is_processed) VALUES (?, ?, ?, ?, 1, 1)",
                     (sid, sender, body, ts))
        insert_transaction(conn, amount=45000, date="2026-02-24", source="sms", source_ref=str(sid), raw_description=desc,
                           card_last4="4321", raw_text=body, ref_no=ref, context="bank")
    run_merge_sweep(conn)
    assert _live(conn) == 1


def test_an_orphan_legacy_row_joins_its_one_same_day_twin(conn, make_card):
    from modules.merge import link_orphan_copies
    make_card("HSBC", "4706", name="Account", active=0)
    conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, is_financial, is_processed) VALUES (?, ?, ?, ?, 1, 1)",
                 (995, "AD-HSBCIN-S", "INR 1000.00 is paid from HSBC account XXXXXX4706 to SAM LEE on 21-Mar-26 with ref 500000000777.",
                  "2026-03-21T07:36:00"))
    orphan = insert_transaction(conn, amount=1000, date="2026-03-21", source="sms", source_ref="994",
                                raw_description="Self Transfer", card_last4="4706", context="bank", link=False)["id"]
    real = insert_transaction(conn, amount=1000, date="2026-03-21", source="sms", source_ref="995", raw_description="SAM LEE",
                              card_last4="4706", context="bank", ref_no="500000000777", link=False)["id"]
    conn.execute("UPDATE transactions SET category = 'Self Transfer' WHERE id IN (?, ?)", (orphan, real))
    conn.commit()
    res = link_orphan_copies(conn, dry_run=False)
    assert res["linked"] and _live(conn) == 1
    assert link_orphan_copies(conn, dry_run=True)["linked"] == []


# --- iteration-198 re-verify (2026-10-20): R1-R4, payee-aware twins, far copies ----------------------

def _sweep2(conn):
    from modules.merge import run_merge_sweep
    run_merge_sweep(conn)
    run_merge_sweep(conn)


def test_r1_yono_sms_then_both_transfers_mails_newest_first(conn, make_card):
    """T1 (10:17): YONO SMS + both mails; T2 (10:47): both mails. The mails arrive in one newest-first
    batch: T2's core-banking mail must not take T1's SMS by `unique` 30 min away."""
    make_card("SBI", "4321", name="Savings", active=0)
    _yono_sms(conn, 1001, "600000101", "2026-10-12T04:47:32")
    for mail in (_ce("61,111.11", "10:47:56"), _ye("600000102", "10:47:36"), _ce("1,11,111.11", "10:17:56"),
                 _ye("600000101", "10:17:36")):
        add_email(conn, *mail)
    run(conn)
    _sweep2(conn)
    assert _live(conn) == 2


def test_r2_core_banking_sms_of_the_second_transfer_then_both_transfers_mails(conn, make_card):
    """T2's core-banking SMS first; then T1's two mails and T2's core-banking mail. T1's YONO mail must
    not take T2's SMS 30 min away: it pairs with its own core-banking mail."""
    make_card("SBI", "4321", name="Savings", active=0)
    _cbs_sms(conn, 1002, "61,111.11", "2026-10-12T05:18:00")
    for mail in (_ye("600000101", "10:17:36"), _ce("1,11,111.11", "10:17:56"), _ce("61,111.11", "10:47:56")):
        add_email(conn, *mail)
    run(conn)
    _sweep2(conn)
    assert _live(conn) == 2




def _yono_sms_to(conn, sid, ref, ts, short, acct):
    body = f"Your Ac Xx4321 debited Rs.50,000.00 for transfer to {short} Ac Xx{acct} dt 12.10.26 Ref {ref}. YONO SBI"
    conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, is_financial, is_processed) VALUES (?, ?, ?, ?, 1, 1)",
                 (sid, "AD-SBYONO-S", body, ts))
    insert_transaction(conn, amount=50000, date="2026-10-12", source="sms", source_ref=str(sid), raw_description=short,
                       card_last4="4321", raw_text=body, ref_no=ref, context="bank")


def _cbs_sms_to(conn, sid, bal, ts, name):
    body = f"Your A/C XXXXX004321 Debited INR 50,000.00 on 12/10/26 -Transferred to {name}. Avl Balance INR {bal}-SBI"
    conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, is_financial, is_processed) VALUES (?, ?, ?, ?, 1, 1)",
                 (sid, "AD-CBSSBI-S", body, ts))
    insert_transaction(conn, amount=50000, date="2026-10-12", source="sms", source_ref=str(sid), raw_description=name,
                       card_last4="4321", raw_text=body, context="bank")


def _cbs_mail_to(bal, t, name):
    return ("cbsalerts.sbi@alerts.sbi.bank.in", "CBSSBI ALERT",
            "Greetings from SBI ! Your A/C XXXXX004321 Debited INR 50,000.00 on 12/10/26 -Transferred to "
            f"{name}. Avl Balance INR {bal}-SBI Please do not reply to this auto generated email.",
            f"Mon, 12 Oct 2026 {t} +0530")


def _yono_mail_to(txn_no, t, name, acct):
    return ("yonobysbi@alerts.sbi.bank.in", "Transaction success",
            "Dear Customer Thank you for using YONO SBI for Fund Transfer The transaction details are as follows: "
            f"Description Details Transaction Status Successful Amount Rs.50,000.00 Transaction Number {txn_no} "
            f"Date of Transaction 12.10.26 Debit account x4321 Beneficiary Name {name} Beneficiary Account "
            f"Number x{acct}", f"Mon, 12 Oct 2026 {t} +0530")


def test_two_transfers_to_different_people_90_seconds_apart_sms_late(conn, make_card):
    """T1 to one person (core-banking SMS + YONO mail), T2 to another 90 s later (YONO SMS + core-banking
    mail), mails first. The two mails name different payees: never one movement (it ended as 3 live)."""
    make_card("SBI", "4321", name="Savings", active=0)
    for mail in (_cbs_mail_to("61,111.11", "10:19:26", "Mr. RAVI KUMAR"),
                 _yono_mail_to("600000101", "10:17:36", "Asha Rani", "7777")):
        add_email(conn, *mail)
    run(conn)
    _cbs_sms_to(conn, 1010, "1,11,111.11", "2026-10-12T04:48:00", "Mrs. ASHA RANI")
    _yono_sms_to(conn, 1011, "600000102", "2026-10-12T04:49:02", "Ravi", "8888")
    _sweep2(conn)
    assert _live(conn) == 2
    for p in conn.execute("SELECT d.raw_description dd, w.raw_description wd FROM transactions d "
                          "JOIN transactions w ON w.id = d.matched_txn_id WHERE d.is_duplicate = 1").fetchall():
        names = (p["dd"] + " " + p["wd"]).lower()
        assert not ("asha" in names and "ravi" in names)



def test_yono_mail_only_and_core_banking_mail_only_2h13m_apart_are_two_transfers(conn, make_card):
    make_card("SBI", "4321", name="Savings", active=0)
    for mail in (_ce("61,111.11", "12:30:56"), _ye("600000101", "10:17:36")):
        add_email(conn, *mail)
    run(conn)
    _sweep2(conn)
    assert _live(conn) == 2


def test_standing_instruction_sms_then_both_mails_is_one_transfer(conn, make_card):
    """The deploy order: both SMS of one standing instruction, then its YONO and core-banking mails."""
    from modules.merge import run_merge_sweep
    make_card("SBI", "4321", name="Savings", active=0)
    yono = ("SBI Ac X4321 debited for SI Fund Transfer of Rs.45,000.00 to . Ac X0001 on 24.02.26 Ref 300000000301. "
            "Not done by you? Call 1800111109.YONO, SBI")
    cbs = ("Your A/C XXXXX004321 Debited INR 45,000.00 on 24/02/26 -Transferred to Mr. SAM. Avl Balance INR 99,219.17-SBI")
    for sid, sender, body, ts, desc, ref in ((1020, "AX-SBYONO-S", yono, "2026-02-24T10:26:44", "Ac X0001", "300000000301"),
                                            (1021, "AD-CBSSBI-S", cbs, "2026-02-24T10:27:20", "Mr. SAM", None)):
        conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, is_financial, is_processed) VALUES (?, ?, ?, ?, 1, 1)",
                     (sid, sender, body, ts))
        insert_transaction(conn, amount=45000, date="2026-02-24", source="sms", source_ref=str(sid), raw_description=desc,
                           card_last4="4321", raw_text=body, ref_no=ref, context="bank")
    add_email(conn, "yonobysbi@alerts.sbi.bank.in", "Transaction success",
              "SBI Ac x4321 debited for SI Fund Transfer of Rs.45,000.00 to . Ac x0001 on 24.02.26 Ref 300000000301.",
              "Tue, 24 Feb 2026 15:56:50 +0530")
    add_email(conn, "cbsalerts.sbi@alerts.sbi.bank.in", "CBSSBI ALERT",
              "Greetings from SBI ! Your A/C XXXXX004321 Debited INR 45,000.00 on 24/02/26 -Transferred to Mr. SAM. "
              "Avl Balance INR 99,219.17-SBI Please do not reply to this auto generated email.",
              "Tue, 24 Feb 2026 15:57:16 +0530")
    run(conn)
    run_merge_sweep(conn)
    assert _live(conn) == 1


def test_two_same_amount_card_mails_in_one_batch_join_their_own_lines(conn, make_card):
    """Round 9 (1437): two Rs 3,141 purchases 35 s apart, each SMS already linked to its statement line
    (the line text names no merchant the alerts do); then both HDFC mails arrive in one batch. Each mail
    prints its purchase's time, so each joins its own line's group: 2 rows, never 4."""
    from modules.merge import _alert_text, _body_facts, _seconds
    make_card("HDFC", "4434")
    for sid, t in ((1070, "22:36:22"), (1071, "22:36:57")):
        body = f"Spent Rs.3141 On HDFC Bank Card 4434 At SAMPLE STORE On 2025-11-23:{t}.Not You?"
        conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, is_financial, is_processed) VALUES (?, ?, ?, ?, 1, 1)",
                     (sid, "JX-HDFCBK-S", body, f"2025-11-23T17:06:{22 if sid == 1070 else 57}"))
        insert_transaction(conn, amount=3141, date="2025-11-23", source="sms", source_ref=str(sid),
                           raw_description="SAMPLE STORE", card_last4="4434", raw_text=body, context="cc")
    for n in (1, 2):
        insert_transaction(conn, amount=3141, date="2025-11-23", source="card", source_ref=f"stmt#{n}",
                           raw_description="EMI PPSL*SWIGGYBANGALORE", card_last4="4434", context="card")
    _sweep2(conn)                                  # the statement's lines now hold the two SMS
    assert _live(conn) == 2
    for t, hdr in (("22:36:57", "Sun, 23 Nov 2025 22:36:58 +0530"), ("22:36:22", "Sun, 23 Nov 2025 22:36:23 +0530")):
        add_email(conn, "alerts@hdfcbank.bank.in", "A payment was made using your Credit Card",
                  "HDFC BANK --> Dear Customer, Greetings from HDFC Bank. We would like to inform you that Rs. 3141.00 has "
                  f"been debited from your HDFC Bank Credit Card ending 4434 towards SAMPLE STORE on 23 Nov, 2025 at {t} . "
                  "To check your available balance", hdr)
    run(conn)
    _sweep2(conn)
    assert _live(conn) == 2
    groups = {}
    for r in conn.execute("SELECT * FROM transactions WHERE is_duplicate = 1 AND source IN ('sms', 'email')"):
        groups.setdefault(r["matched_txn_id"], []).append(_seconds(_body_facts(_alert_text(conn, r))["time"]))
    assert len(groups) == 2 and all(max(v) - min(v) <= 5 for v in groups.values())


def test_a_card_sms_after_its_mail_joins_the_line_holding_that_mail(conn, make_card):
    """Two Rs 3,141 lines first (text naming neither alert), then purchase 1's mail (it takes line 1),
    then purchase 1's SMS and purchase 2's SMS. Purchase 1's SMS prints its mail's time: it joins line 1
    on that proof instead of being paired by arrival order with line 2 (which left purchase 2's SMS live:
    3 rows)."""
    make_card("HDFC", "4434")
    for n in (1, 2):
        insert_transaction(conn, amount=3141, date="2025-11-23", source="card", source_ref=f"stmt#{n}",
                           raw_description="EMI PPSL*SWIGGYBANGALORE", card_last4="4434", context="card")
    add_email(conn, "alerts@hdfcbank.bank.in", "A payment was made using your Credit Card",
              "HDFC BANK --> Dear Customer, Greetings from HDFC Bank. We would like to inform you that Rs. 3141.00 has "
              "been debited from your HDFC Bank Credit Card ending 4434 towards SAMPLE STORE on 23 Nov, 2025 at 22:36:22 . "
              "To check your available balance", "Sun, 23 Nov 2025 22:36:23 +0530")
    run(conn)
    for sid, t in ((1075, "22:36:22"), (1076, "22:36:57")):
        body = f"Spent Rs.3141 On HDFC Bank Card 4434 At SAMPLE STORE On 2025-11-23:{t}.Not You?"
        conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, is_financial, is_processed) VALUES (?, ?, ?, ?, 1, 1)",
                     (sid, "JX-HDFCBK-S", body, f"2025-11-23T17:06:{24 if sid == 1075 else 59}"))
        insert_transaction(conn, amount=3141, date="2025-11-23", source="sms", source_ref=str(sid),
                           raw_description="SAMPLE STORE", card_last4="4434", raw_text=body, context="cc")
    _sweep2(conn)
    assert _live(conn) == 2
