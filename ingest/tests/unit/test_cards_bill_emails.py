"""iter-18 HP5 / M1: bank and CRED bill + payment emails (modules/email/bill_emails.py).

Bodies are the real stored `emails.body_snippet` wordings (HTML-stripped, entities and layout
whitespace kept), with the owner's name replaced."""
from datetime import date

from modules.email.bill_emails import (
    clean_text, email_ist_date, parse_bill_email, parse_payment_email,
)

CRED = "protect@cred.club"
PAD = "                                    "  # stored CRED bodies carry long whitespace runs


def test_clean_text_unescapes_entities_and_collapses_layout():
    assert clean_text("Total Amount Due: &#8377;16,469.81\xa0‌  Pay") == "Total Amount Due: ₹16,469.81 Pay"


def test_email_date_is_the_ist_day():
    assert email_ist_date("Wed, 07 Oct 2026 23:50:00 +0000") == date(2026, 10, 8)
    assert email_ist_date("Mon, 5 Oct 2026 06:48:51 +0530") == date(2026, 10, 5)
    assert email_ist_date("not a date") is None


def test_cred_due_email_gives_total_and_due_date():
    body = (f"{PAD}Customer Name,{PAD}your credit card payment{PAD}is due{PAD}HDFC Bank •••• 4956 Due amount Due date "
            "&#8377;13,836 Oct 21, 2026 pay before the due date to avoid late fees &amp; protect your credit score.")
    f = parse_bill_email(CRED, "Your credit card bill is due on Oct 21, 2026: Pay now to maintain your", body, date(2026, 10, 19))
    assert (f["card_last4"], f["total_due"], f["due_date"], f["authority"]) == ("4956", 13836.0, "2026-10-21", "cred")


def test_cred_remaining_due_is_never_a_statement_total():
    body = ("Customer Name, finish paying your credit card bill ICICI Bank •••• 0026 Remaining due Due date "
            "₹58,766.63 Sep 07, 2026 pay before the due date to avoid interest charges")
    f = parse_bill_email(CRED, "Your credit card bill is due on Sep 07, 2026", body, date(2026, 9, 3))
    assert f["total_due"] is None and f["due_date"] == "2026-09-07" and f["card_last4"] == "0026"
    old = ("CRED Tuesday, Mar 10, 2026 hi, finish paying your credit card bill payment due by Mar 10, 2026 "
           "ICICI Bank .... xxxx 0026 remaining due ₹101372.79 Pay now we recommend to pay atleast 2 days before to")
    f = parse_bill_email(CRED, "Your credit card bill is due on Mar 10, 2026", old, date(2026, 3, 10))
    assert f["total_due"] is None and f["due_date"] == "2026-03-10"


def test_cred_old_due_email():
    body = ("CRED Monday, Sep 29, 2025 hi, your credit card payment is due payment due by Oct 03, 2025 "
            "Axis Bank .... xxxx 9320 total due ₹419.23 Pay now we recommend to pay atleast 2 days before")
    f = parse_bill_email(CRED, "Your credit card bill is due on Oct 03, 2025", body, date(2025, 9, 29))
    assert (f["card_last4"], f["total_due"], f["due_date"]) == ("9320", 419.23, "2025-10-03")


def test_cred_bill_snippet_cut_before_the_year():
    body = ("Customer Name, your credit card bill has been generated Here is your October Bill ICICI Bank "
            "XXXX-XXXX-XXXX-8013 total amount due minimum amount due due date ₹22017.41 ₹12350.00 November 07,")
    f = parse_bill_email(CRED, "Important : New statement for your ICICI Bank card XXXX-8013 is here", body, date(2025, 11, 6))
    assert (f["card_last4"], f["total_due"], f["min_due"], f["due_date"]) == ("8013", 22017.41, 12350.0, "2025-11-07")


def test_cred_new_bill_email():
    body = (f"{PAD}Customer Name,{PAD}your credit card bill for October has{PAD}been generated{PAD}Axis Bank •••• 9320 "
            "bill summary total amount due &#8377;1,051.38 minimum due &#8377;100.00 due date November 01, 2026 "
            "bill generated on Oct 11, 2026 pay before the due date to avoid interest charges")
    f = parse_bill_email(CRED, "Important : New bill for your Axis Bank card XXXX-9320 is here", body, date(2026, 10, 13))
    assert (f["card_last4"], f["total_due"], f["min_due"], f["due_date"]) == ("9320", 1051.38, 100.0, "2026-11-01")
    assert f["template"] == "cred-new-bill"


def test_cred_utility_bill_is_not_a_card_bill():
    body = ("Customer Name, your electricity bill from Andhra Pradesh Central Power Distribution Corporation "
            "Limited is here and due soon. total amount due ₹1,172.00 due date 22 Oct '26 pay now")
    assert parse_bill_email(CRED, "your electricity bill is due soon", body, date(2026, 10, 18)) is None
    assert parse_payment_email(CRED, "your electricity bill is due soon", body, date(2026, 10, 18)) is None


def test_icici_statement_email_variants():
    body = ("Credit Card Statement Payment due by October 8, 2026 ICICI Bank Credit Card XX0026 Minimum Amount Due: "
            "₹5,440.00 Total Amount Due: &#8377;16,469.81 Pay now using: iMobile Net Banking UPI*")
    f = parse_bill_email("credit_cards@icici.bank.in", "Amazon Pay ICICI Bank Credit Card Statement", body, date(2026, 9, 21))
    assert (f["card_last4"], f["total_due"], f["min_due"], f["due_date"], f["authority"]) == \
        ("0026", 16469.81, 5440.0, "2026-10-08", "bank")
    old = ("Payment due by November 7, 2025 ICICI Bank Credit Card XX0026 Minimum Amount Due: ₹410.00 "
           "Total Amount Due &#8377;8,167.11 Pay now:")
    assert parse_bill_email("credit_cards@icicibank.com", "Statement", old, date(2025, 10, 21))["total_due"] == 8167.11
    credit = ("Payment due by February 7, 2026 ICICI Bank Credit Card XX1019 Minimum Amount Due: ₹0.00 "
              "Total Amount Due ₹1,000.48 CR Pay now:")
    assert parse_bill_email("credit_cards@icicibank.com", "Statement", credit, date(2026, 1, 21))["total_due"] == -1000.48


def test_sbi_statement_email_with_and_without_payment_due():
    body = ("Untitled Document Dear Cardholder Attached herewith is the monthly statement of your SBI Credit Card "
            "ending with XXXX XXXX XXXX XX11. Please note that your statement is password protected. Given below is "
            "the summary of your Statement for a quick reference. Total amount due ( ) 46,088.00 Minimum amount due "
            "( ) 5,598.00 Payment due date 09-Oct-2026 Note: We request you")
    f = parse_bill_email("statements@sbicard.com", "Your CASHBACK SBI Card Monthly Statement -Sep 2026", body, date(2026, 9, 20))
    assert (f["card_suffix"], f["bank"], f["total_due"], f["min_due"], f["due_date"]) == ("11", "SBI", 46088.0, 5598.0, "2026-10-09")
    aurum = ("Dear AURUM Cardholder Attached herewith is the monthly statement of your AURUM Card ending with "
             "XXXX XXXX XXXX XX71. Given below is the summary of your Statement for a quick reference. Total amount "
             "due ( ) 0.00 Minimum amount due ( ) 0.00 Payment due date NO PAYMENT REQUIRED Note:")
    f = parse_bill_email("aurumcardstatement@sbicard.com", "Your AURUM Card Monthly Statement -Aug 2026", aurum, date(2026, 8, 20))
    assert (f["card_suffix"], f["total_due"], f["due_date"], f["statement_date"]) == ("71", 0.0, None, "2026-08-20")


def test_axis_statement_email():
    body = ("Axis Bank Dear Customer, Please find enclosed your credit card statement for OCTOBER 2026. Total Amount "
            "Due INR Minimum Amount Due (INR) Payment Due Date (DD-MM-YYYY) 1051.38 Dr 100 Dr 01/11/2026 How do I")
    f = parse_bill_email("cc.statements@axis.bank.in",
                         "Airtel Axis Bank Mastercard Credit Card Statement ending XX20 - October 2026", body, date(2026, 10, 14))
    assert (f["card_suffix"], f["bank"], f["total_due"], f["min_due"], f["due_date"]) == ("20", "Axis", 1051.38, 100.0, "2026-11-01")


def test_payment_confirmations():
    cred = (f"{PAD}Customer Name,{PAD}here is your payment{PAD}confirmation{PAD}Your credit card payment was successful"
            f"{PAD}in 44 seconds{PAD}ICICI Bank •••• 0026 payment details amount paid &#8377;16,469.81 payment date "
            "Oct 07, 2026 credited to card Oct 07, 2026 transaction summary Order ID: Gqzzzzzzz005")
    p = parse_payment_email(CRED, "your credit card bill payment was successful", cred, date(2026, 10, 7))
    assert (p["card_last4"], p["amount"], p["payment_date"], p["authority"], p["ref"]) == \
        ("0026", 16469.81, "2026-10-07", "cred", "cred:Gqzzzzzzz005")
    icici = ("&nbsp; Dear Customer, Oct 07,2026 &nbsp; &nbsp; Greetings from ICICI Bank! We have received payment of "
             "INR 16,469.81 on your ICICI Bank Credit Card account 4315 XXXX XXXX 0026 on 07-Oct-2026. Looking forward")
    p = parse_payment_email("credit_cards@icici.bank.in", "Payment received on your ICICI Bank Credit Card.", icici, date(2026, 10, 7))
    assert (p["card_last4"], p["amount"], p["payment_date"], p["authority"]) == ("0026", 16469.81, "2026-10-07", "bank")


def test_card_spend_alert_is_not_a_payment():
    body = ("HDFC BANK --> Dear Customer, Greetings from HDFC Bank. We would like to inform you that Rs. 932.00 has "
            "been debited from your HDFC Bank Credit Card ending 9434 towards RAZ*Swiggy on 17 Oct, 2026")
    assert parse_payment_email("alerts@hdfcbank.bank.in", "A payment was made using your Credit Card", body) is None
    assert parse_bill_email("alerts@hdfcbank.bank.in", "A payment was made using your Credit Card", body) is None
