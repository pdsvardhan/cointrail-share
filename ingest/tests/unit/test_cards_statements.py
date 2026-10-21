"""iter-18 H1 / M15 / HP5: statement totals, bank due dates and minimum dues from bank SMS, bank and
CRED emails and the record_bank_statement contract — with bank-over-CRED precedence."""
import pytest

import services.cards_service as cs

_n = [0]


def add_sms(conn, body, ts, sender="AD-BANK-S"):
    return conn.execute("INSERT INTO sms (sender, body, phone_timestamp) VALUES (?, ?, ?)",
                        (sender, body, ts)).lastrowid


def add_email(conn, sender, subject, body, date_header):
    _n[0] += 1
    return conn.execute(
        "INSERT INTO emails (gmail_id, sender, sender_email, subject, body_snippet, date) VALUES (?, ?, ?, ?, ?, ?)",
        (f"test-{_n[0]}", sender, sender, subject, body, date_header)).lastrowid


def stmt(conn, last4, billing_end):
    return conn.execute(
        "SELECT s.* FROM statements s JOIN cards c ON c.id=s.card_id WHERE c.last4=? AND s.billing_end=?",
        (last4, billing_end)).fetchone()


AXIS_OCT = ("Your statement for Axis Bank Credit Card no. XX9320 is generated.\nDue on: 01-11-26\nTotal amt: INR  Dr. 1051.38\n"
            "Min amt due: INR  Dr. 100.00\nPay at axis.bank.in/ccpaynow\nTo view fees & charges: axis.bank.in/kfs")
AXIS_CR = ("Statement for your Axis Bank Credit Card no. XX9320 has been generated.\nDue on: 01-01-26\nTotal amt: INR  Cr. 199.00\n"
           "Min amt due: INR   0.00\nTo pay, visit axisbank.com/ccpaynow\nTo view the fees & charges, visit https://ccm.axbk.in/AXISBK/Aqzzz007")
AXIS_ESTMT_CR = ("E-Statement of your Axis Bank Credit Card no. XX9320 has been generated. Total amount due: INR  Cr. 137.00. "
                 "Minimum amt due: INR   0.00, Due date: 01-06-25. Visit https://ccm.axbk.in/AXISBK/Bqzzz008 to view / download.")


# --- parse: every statement / reminder wording seen in the owner's SMS ----------------------------

@pytest.mark.parametrize("body, expect", [
    (AXIS_OCT, ("9320", 1051.38, "2026-11-01", 100.0, "sms-axis")),
    (AXIS_CR, ("9320", -199.0, "2026-01-01", 0.0, "sms-axis")),
    (AXIS_ESTMT_CR, ("9320", -137.0, "2025-06-01", 0.0, "sms-axis")),
    ("E-Statement of your Axis Bank Credit Card no. XX9320 has been generated. Total amount due: INR  Dr. 662.00. "
     "Minimum amt due: INR  Dr. 100.00, Due date: 02-07-25. Visit https://ccm.axbk.in/AXISBK/nqzzz009 to view / download.\n",
     ("9320", 662.0, "2025-07-02", 100.0, "sms-axis")),
    ("Statement for your Axis Bank Credit Card XX9320 of INR 6298.35 has been generated with due date 01-04-25. View the "
     "transaction details @ https://ccm.axbk.in/AXISBK/oqzzz010 ; Minimum amount due is INR 705.",
     ("9320", 6298.35, "2025-04-01", 705.0, "sms-axis")),
    ("HDFC Bank Credit Card XX9434 Statement:\nTotal due: Rs.18,691.00\nMin.due: Rs.940.00\nPay by 11-06-2026\nView: https://1.hdfc.bank.in/HDFCBK/v/Eq0019",
     ("9434", 18691.0, "2026-06-11", 940.0, "sms-hdfc")),
    ("HDFC Bank Credit Card XX4956 Statement: Total due amt: Rs.8,630.00 Min due amt: Rs.440.00 Due by:21-08-2025. View statement here:https://hdfcbk.io/HDFCBK/K/HDFCBK/Xqzzzzzz011",
     ("4956", 8630.0, "2025-08-21", 440.0, "sms-hdfc")),
    ("HDFC Bank Credit Card XX7180 Statement:\nTotal due: Rs.-0.46\nMin.due: Rs.0.00\nPay by Nil\nView: https://1.hdfc.bank.in/HDFCBK/v/Eq0020",
     ("7180", -0.46, None, 0.0, "sms-hdfc")),
    ("Amount Due\nRs.18691 on HDFC Bank Credit Card 9434. Pay instantly by 11/JUN/2026 via PayZapp > Bill Pay > Credit Card: https://hdfcbk.io/HDFCBK/s/Zqzzz012",
     ("9434", 18691.0, "2026-06-11", None, "sms-hdfc-reminder")),
    ("Amt Due: Rs 11865 on HDFC Bank Credit Card 9434. Pay Instant with PayZapp. Steps: PayZapp-Bill-Credit Card Pay Now: https://hdfcbk.io/HDFCBK/s/gqzzz013",
     ("9434", 11865.0, None, None, "sms-hdfc-reminder")),
    ("Dear Customer, Amt Due Rs.9987 on HDFC Bank Card X9434 Pay with PayZapp, your safe Bank zone. Steps: PayZapp-Bill-Credit Card Pay Now: https://hdfcbk.io/HDFCBK/s/Pqzzz014",
     ("9434", 9987.0, None, None, "sms-hdfc-reminder")),
    ("ICICI Bank Credit Card XX0026 Statement is sent to xx*******x0@gmail.com. Total of Rs 16,469.81 or minimum of Rs 5,440.00 is due by 08-OCT-26.",
     ("0026", 16469.81, "2026-10-08", 5440.0, "sms-icici")),
    ("Pay Total Amount Due of Rs 16,460.31 or Minimum Amount Due of Rs 5,430.50 by 08-Oct-26 towards ICICI Bank Credit Card XX0026. Delay/Non-payment is reported to Credit Bureaus. Ignore if paid.",
     ("0026", 16460.31, "2026-10-08", 5430.5, "sms-icici-reminder")),
    ("Pay Total Due of Rs 1,06,499.13 or Minimum Due Rs 5,325.61 by 07-Sep-25 for ICICI Bank Credit Card XX0026. Delayed/No payments are reported to Credit Bureaus.",
     ("0026", 106499.13, "2025-09-07", 5325.61, "sms-icici-reminder")),
    ("Kindly pay total due of Rs 33366.5 or Min Due Rs 1670 by 23-Feb-25 on ICICI Bank Credit Card XX0026 to avoid reporting to credit bureaus",
     ("0026", 33366.5, "2025-02-23", 1670.0, "sms-icici-reminder")),
    ("Payment of INR 2411.32 for Axis Bank Credit Card no. XX9320 is due on 01-08-26 with minimum amount due of INR 100. Ignore if paid.",
     ("9320", 2411.32, "2026-08-01", 100.0, "sms-axis-reminder")),
    ("Dear SBI Cardholder, outstanding of Rs. 1488.00, on your credit card ending 2111 is due on 08-SEP-25. Min. Amount Due: Rs. 200.00. Please ignore if already paid.",
     ("2111", 1488.0, "2025-09-08", 200.0, "sms-sbi-reminder")),
    ("Dear Customer, total payment due on HSBC credit card ending with 8938 is INR 4240.06 and minimum payment due is INR 100. To enjoy uninterrupted card usage, please ensure payment is realized by due date 22-Mar-25.",
     ("8938", 4240.06, "2025-03-22", 100.0, "sms-hsbc")),
    ("Your credit card bill for SBI XXXX-2111 has been generated.\n\nTotal amount: INR 17,152.00\nDue date: February 08, 2026\n\nTap on the link to pay: https://link.cred.club/CREDIN/link/mqzzz015 and avoid late payment fees. - CRED",
     ("2111", 17152.0, "2026-02-08", None, "sms-cred")),
])
def test_parse_statement_sms(body, expect):
    f = cs.parse_statement_sms(body)
    assert (f["last4"], f["total"], f["due"], f["min_due"], f["source"]) == expect


def test_parse_sbi_estatement_gives_suffix_and_min_due():
    f = cs.parse_statement_sms(
        "E-statement of SBI Credit Card ending XX11 dated 19/09/2026 has been mailed. If not received, SMS ENRS to 5676791. "
        "Total Amt Due Rs 46088; Min Amt Due Rs 5598; Payable by 09/10/2026. Click https://sbicard.com/quickpaynet to pay your bill  \n")
    assert (f["suffix"], f["total"], f["due"], f["min_due"], f["source"]) == ("11", 46088.0, "2026-10-09", 5598.0, "sms-sbi")


def test_non_statement_sms_is_ignored():
    assert cs.parse_statement_sms("Your Airtel Axis Bank Mastercard Credit Card XX9320 bill of INR 1211.9 is overdue. "
                                  "Pls make min payment of INR -100 to continue using your Credit Card.") is None
    assert cs.parse_statement_sms("Reversal of Rs 4.01 credited to ICICI Bank Credit Card XX0026 on 03-APR-26. "
                                  "Revised total due Rs 0, minimum due Rs .00") is None


# --- H1: the bank's due date and minimum due are stored ----------------------------------------------

def test_statement_sms_stores_bank_due_date_and_min_due(conn, make_card):
    make_card("Axis", "9320", statement_day=14, due_day=5)
    add_sms(conn, AXIS_OCT, "2026-10-14T04:30:00")
    cs.statements_from_sms_logic(conn)
    s = stmt(conn, "9320", "2026-10-14")
    assert (s["stated_total"], s["due_date"], s["due_date_source"], s["min_due"]) == (1051.38, "2026-11-01", "bank", 100.0)


def test_axis_credit_balance_statement_is_a_negative_total(conn, make_card):   # M15
    make_card("Axis", "9320", statement_day=14, due_day=5)
    add_sms(conn, AXIS_CR, "2025-12-14T05:00:00")
    add_sms(conn, AXIS_ESTMT_CR, "2025-05-13T13:07:35")
    cs.statements_from_sms_logic(conn)
    dec = stmt(conn, "9320", "2025-12-14")
    assert (dec["stated_total"], dec["min_due"], dec["due_date"]) == (-199.0, 0.0, "2026-01-01")
    assert stmt(conn, "9320", "2025-05-14")["stated_total"] == -137.0


def test_hdfc_nil_statement_is_placed_by_its_arrival_day(conn, make_card):
    make_card("HDFC", "7180", statement_day=22, due_day=12)
    add_sms(conn, "HDFC Bank Credit Card XX7180 Statement:\nTotal due: Rs.-0.46\nMin.due: Rs.0.00\nPay by Nil\n"
                  "View: https://1.hdfc.bank.in/HDFCBK/v/Eq0020", "2026-04-23T12:19:30")
    cs.statements_from_sms_logic(conn)
    s = stmt(conn, "7180", "2026-04-22")
    assert (s["stated_total"], s["due_date"], s["min_due"]) == (-0.46, None, 0.0)


def test_hdfc_dated_reminder_never_replaces_the_statement_figure(conn, make_card):
    make_card("HDFC", "9434", statement_day=22, due_day=12)
    add_sms(conn, "HDFC Bank Credit Card XX9434 Statement:\nTotal due: Rs.18,691.00\nMin.due: Rs.940.00\nPay by 11-06-2026\n"
                  "View: https://1.hdfc.bank.in/HDFCBK/v/Eq0019", "2026-05-23T12:44:42")
    add_sms(conn, "Amount Due\nRs.18690 on HDFC Bank Credit Card 9434. Pay instantly by 11/JUN/2026 via PayZapp > Bill Pay > "
                  "Credit Card: https://hdfcbk.io/HDFCBK/s/Zqzzz012", "2026-05-23T13:13:39")
    cs.statements_from_sms_logic(conn)
    s = stmt(conn, "9434", "2026-05-22")
    assert (s["stated_total"], s["total_source"], s["due_date"], s["min_due"]) == (18691.0, "sms-hdfc", "2026-06-11", 940.0)


def test_hdfc_dateless_reminder_fills_the_statement_that_just_closed(conn, make_card):
    # Rule: filed on the statement that most recently closed on or before the SMS's IST day; it only
    # fills a missing or CRED-relayed total and never sets the due date.
    make_card("HDFC", "9434", statement_day=22, due_day=12)
    add_sms(conn, "Amt Due: Rs 11865 on HDFC Bank Credit Card 9434. Pay Instant with PayZapp. Steps: PayZapp-Bill-Credit "
                  "Card Pay Now: https://hdfcbk.io/HDFCBK/s/gqzzz013", "2025-07-23T08:04:43")
    cs.statements_from_sms_logic(conn)
    s = stmt(conn, "9434", "2025-07-22")
    assert (s["stated_total"], s["total_source"], s["due_date"]) == (11865.0, "sms-hdfc-reminder", None)


def test_hdfc_dateless_reminder_loses_to_the_statement_but_beats_cred(conn, make_card):
    make_card("HDFC", "9434", statement_day=22, due_day=12)
    make_card("HDFC", "7180", statement_day=22, due_day=12)
    add_sms(conn, "HDFC Bank Credit Card XX9434 Statement: Total due amt: Rs.11,865.00 Min due amt: Rs.600.00 Due by:11-08-2025. "
                  "View statement here:https://hdfcbk.io/HDFCBK/K/HDFCBK/Xqzzzzzz016", "2025-07-23T07:50:00")
    add_sms(conn, "Amt Due: Rs 11800 on HDFC Bank Credit Card 9434. Pay Instant with PayZapp.", "2025-07-23T08:04:43")
    add_sms(conn, "Your credit card bill for HDFC XXXX-7180 has been generated.\n\nTotal amount: INR 11,000.00\nDue date: "
                  "August 11, 2025\n\nTap on the link to pay: https://link.cred.club/link/x and avoid late payment fees. - CRED",
            "2025-07-23T06:00:00")
    add_sms(conn, "Amt Due: Rs 11171 on HDFC Bank Credit Card 7180. Pay Instant with PayZapp.", "2025-07-23T07:54:44")
    cs.statements_from_sms_logic(conn)
    assert stmt(conn, "9434", "2025-07-22")["stated_total"] == 11865.0           # statement wins
    s = stmt(conn, "7180", "2025-07-22")
    assert (s["stated_total"], s["due_date"], s["due_date_source"]) == (11171.0, "2025-08-11", "cred")  # bank > CRED


def test_bank_statement_beats_a_newer_cred_message(conn, make_card):
    make_card("SBI", "2111", statement_day=19, due_day=8, pdf_card_last4="9181")
    add_sms(conn, "E-statement of SBI Credit Card ending XX11 dated 19/09/2026 has been mailed. If not received, SMS ENRS to "
                  "5676791. Total Amt Due Rs 46088; Min Amt Due Rs 5598; Payable by 09/10/2026. Click https://sbicard.com/quickpaynet",
            "2026-09-20T23:04:29")
    add_sms(conn, "Your credit card bill for SBI XXXX-2111 has been generated.\n\nTotal amount: INR 46,089.00\nDue date: October 09, "
                  "2026\n\nTap on the link to pay: https://link.cred.club/CREDIN/link/x and avoid late payment fees. - CRED",
            "2026-09-21T05:00:00")
    cs.statements_from_sms_logic(conn)
    s = stmt(conn, "2111", "2026-09-19")
    assert (s["stated_total"], s["total_source"], s["due_date_source"], s["min_due"]) == (46088.0, "sms-sbi", "bank", 5598.0)


def test_icici_reminder_does_not_override_the_statement_total(conn, make_card):
    make_card("ICICI", "0026", statement_day=21, due_day=10)
    add_sms(conn, "ICICI Bank Credit Card XX0026 Statement is sent to xx*******x0@gmail.com. Total of Rs 16,469.81 or minimum of "
                  "Rs 5,440.00 is due by 08-OCT-26.", "2026-09-27T05:00:00")
    add_sms(conn, "Pay Total Amount Due of Rs 16,460.31 or Minimum Amount Due of Rs 5,430.50 by 08-Oct-26 towards ICICI Bank Credit "
                  "Card XX0026. Delay/Non-payment is reported to Credit Bureaus. Ignore if paid.", "2026-10-01T06:11:36")
    cs.statements_from_sms_logic(conn)
    s = stmt(conn, "0026", "2026-09-21")
    assert (s["stated_total"], s["min_due"], s["due_date"]) == (16469.81, 5440.0, "2026-10-08")


# --- HP5: bank and CRED emails -----------------------------------------------------------------------

CRED_4956 = ("Customer Name, your credit card payment is due HDFC Bank •••• 4956 Due amount Due date &#8377;13,836 "
             "Oct 21, 2026 pay before the due date to avoid late fees &amp; protect your credit score.")


def test_cred_due_email_fills_a_statement_and_the_bank_then_replaces_it(conn, make_card):
    make_card("HDFC", "4956", statement_day=1, due_day=21)
    add_email(conn, "protect@cred.club", "Your credit card bill is due on Oct 21, 2026", CRED_4956, "Mon, 19 Oct 2026 13:02:34 +0530")
    cs.statements_from_emails_logic(conn)
    s = stmt(conn, "4956", "2026-10-01")
    assert (s["stated_total"], s["total_source"], s["due_date"], s["due_date_source"]) == (13836.0, "email-cred", "2026-10-21", "cred")
    assert (s["month"], s["year"]) == (9, 2026)          # stmt_day 1 cards are labelled by spending month
    cs.record_bank_statement(conn, "4956", 13836.54, "2026-10-21", "pdf-hdfc", min_due=700)
    s = stmt(conn, "4956", "2026-10-01")
    assert (s["stated_total"], s["total_source"], s["due_date_source"], s["min_due"]) == (13836.54, "pdf-hdfc", "bank", 700.0)
    cs.statements_from_emails_logic(conn)                 # the CRED email again: no effect now
    assert stmt(conn, "4956", "2026-10-01")["stated_total"] == 13836.54


def test_cred_remaining_due_only_sets_the_due_date(conn, make_card):
    make_card("ICICI", "0026", statement_day=21, due_day=10)
    add_email(conn, "protect@cred.club", "Your credit card bill is due on Sep 07, 2026",
              "Customer Name, finish paying your credit card bill ICICI Bank •••• 0026 Remaining due Due date ₹58,766.63 "
              "Sep 07, 2026 pay before the due date", "Thu, 03 Sep 2026 12:19:24 +0530")
    cs.statements_from_emails_logic(conn)
    assert stmt(conn, "0026", "2026-08-21") is None       # a remaining amount never invents a statement
    conn.execute("INSERT INTO statements (card_id, month, year, total_amount, computed_total, billing_start, billing_end, status) "
                 "SELECT id, 8, 2026, 23884.76, 23884.76, '2026-07-22', '2026-08-21', 'parsed' FROM cards WHERE last4='0026'")
    cs.statements_from_emails_logic(conn)
    s = stmt(conn, "0026", "2026-08-21")
    assert (s["stated_total"], s["due_date"], s["due_date_source"]) == (None, "2026-09-07", "cred")


def test_issuer_statement_emails(conn, make_card):
    make_card("ICICI", "0026", statement_day=21, due_day=10)
    make_card("SBI", "2971", statement_day=19, due_day=8, pdf_card_last4="8002")
    make_card("Axis", "9320", statement_day=14, due_day=5)
    add_email(conn, "credit_cards@icici.bank.in", "Amazon Pay ICICI Bank Credit Card Statement for the period August 21, 2026",
              "Credit Card Statement Payment due by October 8, 2026 ICICI Bank Credit Card XX0026 Minimum Amount Due: ₹5,440.00 "
              "Total Amount Due: &#8377;16,469.81 Pay now using: iMobile", "Mon, 21 Sep 2026 09:30:00 +0530")
    add_email(conn, "aurumcardstatement@sbicard.com", "Your AURUM Card Monthly Statement -Aug 2026",
              "Dear AURUM Cardholder Attached herewith is the monthly statement of your AURUM Card ending with XXXX XXXX XXXX XX71. "
              "Total amount due ( ) 0.00 Minimum amount due ( ) 0.00 Payment due date NO PAYMENT REQUIRED Note:",
              "Thu, 20 Aug 2026 10:00:00 +0530")
    add_email(conn, "cc.statements@axis.bank.in", "Airtel Axis Bank Mastercard Credit Card Statement ending XX20 - October 2026",
              "Axis Bank Dear Customer, Please find enclosed your credit card statement for OCTOBER 2026. Total Amount Due INR "
              "Minimum Amount Due (INR) Payment Due Date (DD-MM-YYYY) 1051.38 Dr 100 Dr 01/11/2026 How do I",
              "Wed, 14 Oct 2026 09:00:00 +0530")
    res = cs.statements_from_emails_logic(conn)
    assert res["facts"] == 3 and res["unresolved_card"] == 0
    s = stmt(conn, "0026", "2026-09-21")
    assert (s["stated_total"], s["min_due"], s["due_date"], s["due_date_source"], s["total_source"]) == \
        (16469.81, 5440.0, "2026-10-08", "bank", "email-icici")
    s = stmt(conn, "2971", "2026-08-19")
    assert (s["stated_total"], s["due_date"]) == (0.0, None)
    assert stmt(conn, "9320", "2026-10-14")["stated_total"] == 1051.38


def test_addon_card_bill_lands_on_the_primary_statement(conn, make_card):
    primary = make_card("ICICI", "1019", statement_day=21, due_day=10)
    make_card("ICICI", "8013", statement_day=21, due_day=10)
    conn.execute("UPDATE cards SET billing_group='sapphiro_group' WHERE last4 IN ('1019', '8013')")
    add_email(conn, "protect@cred.club", "Important : New statement for your ICICI Bank card XXXX-8013 is here",
              "Customer Name, your credit card bill has been generated Here is your November Bill ICICI Bank "
              "XXXX-XXXX-XXXX-8013 total amount due minimum amount due due date ₹11811.47 ₹11811.47 December 08,",
              "Fri, 21 Nov 2025 10:00:00 +0530")
    cs.statements_from_emails_logic(conn)
    s = stmt(conn, "1019", "2025-11-21")
    assert s is not None and s["card_id"] == primary and (s["stated_total"], s["due_date"]) == (11811.47, "2025-12-08")
    assert stmt(conn, "8013", "2025-11-21") is None


# --- contract 1: record_bank_statement ---------------------------------------------------------------

def test_record_bank_statement_contract(conn, make_card):
    make_card("SBI", "2111", statement_day=19, due_day=8, pdf_card_last4="9181")
    assert cs.record_bank_statement(conn, "9181", 46088, "2026-10-09", "pdf-sbi", min_due=5598) == 1   # PDF alias
    assert cs.record_bank_statement(conn, "2111", 46088, "2026-10-09", "pdf-sbi", min_due=5598) == 0   # unchanged
    s = stmt(conn, "2111", "2026-09-19")
    assert (s["stated_total"], s["due_date"], s["due_date_source"], s["min_due"], s["total_source"]) == \
        (46088.0, "2026-10-09", "bank", 5598.0, "pdf-sbi")
    assert cs.record_bank_statement(conn, "2111", -12.5, None, "pdf-sbi", statement_date_iso="2026-08-19") == 1
    assert stmt(conn, "2111", "2026-08-19")["stated_total"] == -12.5   # credit balance
    assert cs.record_bank_statement(conn, "0000", 10, "2026-10-09", "pdf-sbi") == 0      # not a card of ours
    with pytest.raises(ValueError):
        cs.record_bank_statement(conn, "2111", None, "2026-10-09", "pdf-sbi")


def test_window_dedup_keeps_the_bank_due_date(conn, make_card):
    card = make_card("HDFC", "4956", statement_day=1, due_day=21)
    conn.execute("INSERT INTO statements (card_id, month, year, total_amount, stated_total, billing_start, billing_end) "
                 "VALUES (?, 9, 2026, 13836, 13836, '2026-09-02', '2026-10-01')", (card,))
    conn.execute("INSERT INTO statements (card_id, month, year, total_amount, billing_start, billing_end, due_date, due_date_source, min_due) "
                 "VALUES (?, 10, 2026, 13836.54, '2026-09-02', '2026-10-01', '2026-10-21', 'bank', 700)", (card,))
    cs.reconcile_statements_logic(conn=conn)
    rows = conn.execute("SELECT stated_total, due_date, min_due FROM statements WHERE card_id=?", (card,)).fetchall()
    assert [tuple(r) for r in rows] == [(13836.0, "2026-10-21", 700.0)]
