"""iter-18 SMS parser: H6 (Axis rewording), H5 (bank refunds/reversals/cashback), M5/M6 (SBI e-mandate,
SBI savings, HSBC account debits), M7 (currency), M8/H8 (dates), M4 (reference numbers).

Bodies are real wordings from the 2026-10-20 corpus; person names, phone numbers and reference numbers
are replaced with fakes."""
from datetime import date

import pytest

from modules.sms.parser import normalize_amount, normalize_date, parse_single_sms, extract_ref_no
from util.fx import to_inr_estimate, APPROX_INR_PER_UNIT


# --- M8 / H8: dates -------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("03/OCT/2026", "2026-10-03"),
    ("15/AUG/2026", "2026-08-15"),
    ("07-Oct-2026.", "2026-10-07"),       # e-mandate "Date: 07-Oct-2026. Time:"
    ("18Oct26", "2026-10-18"),            # SBI UPI "on date 18Oct26"
    ("27JUL25", "2025-07-27"),            # SBI INB
    ("18 Oct 26", "2026-10-18"),
    ("2 Nov 25", "2025-11-02"),
    ("18.10.26", "2026-10-18"),           # YONO "dt 19.05.26"
    ("18-10-26,", "2026-10-18"),
    ("05-Oct-26;", "2026-10-05"),
    ("2026-10-17:22:52:32.Not", "2026-10-17"),
    ("1-11-2026", "2026-11-01"),
    ("8-feb-26", "2026-02-08"),
    ("Sun Apr 20 2025", "2025-04-20"),    # Pluxee
    ("February 07, 2026", "2026-02-07"),  # CRED
    ("31-02-26", None),                   # not a real date
    ("", None),
])
def test_normalize_date_formats(raw, expected):
    assert normalize_date(raw, date(2026, 10, 19)) == expected


def test_year_less_dates_take_the_year_from_the_sms_receive_date():
    # HSBC "on 31DEC" read on 2 January belongs to the previous year — the old code used the
    # server's current year and produced a date eleven months in the future.
    assert normalize_date("31DEC", date(2027, 1, 2)) == "2026-12-31"
    assert normalize_date("13OCT", date(2026, 10, 19)) == "2026-10-13"
    assert normalize_date("13OCT", "2026-10-19") == "2026-10-13"
    # HDFC RuPay-on-UPI prints "On 30-04"
    assert normalize_date("30-04", date(2026, 5, 2)) == "2026-04-30"
    assert normalize_date("30-12", date(2027, 1, 3)) == "2026-12-30"


def test_amount_formats():
    assert normalize_amount("1,23,456.78") == 123456.78
    assert normalize_amount("500/-") == 500.0
    assert normalize_amount("75.64-") == 75.64        # HSBC debit
    assert normalize_amount("20,805.00+") == 20805.0   # HSBC credit


@pytest.mark.parametrize("body,amount", [
    ("Spent ₹500.00 On HDFC Bank Card 9434 At AMAZON On 2026-10-18:12:00:00.Not You?", 500.0),
    ("Spent Rs 500 On HDFC Bank Card 9434 At AMAZON On 2026-10-18:12:00:00.Not You?", 500.0),
    ("Rs.500/- spent on your SBI Credit Card ending 2111 at AMAZON on 18/10/26. Trxn. not done by you?", 500.0),
    ("Rs. 1,500.00 spent on your SBI Credit Card ending 2111 at AMAZON on 18/10/26.", 1500.0),
    ("INR500.00 spent using ICICI Bank Card XX0026 on 18-Oct-26 on AMAZON. Avl Limit: INR 3,15,528.37.", 500.0),
])
def test_rupee_spellings(body, amount):
    p = parse_single_sms(body)
    assert p and p["amount"] == amount and p["currency"] == "INR"


# --- H6: Axis -------------------------------------------------------------------------------------

def test_axis_2026_10_wording():
    p = parse_single_sms("Spent INR 979 \nusing Axis Bank Credit Card no. XX9320 on 19-10-26 01:49:17 IST at \n"
                         "AIRTEL PAYM. \nAvl limit: INR 157920.62. Not you? SMS BLOCK 9320 to +910000000001.")
    assert p["pattern"] == "axis_spent_using"
    assert (p["amount"], p["card_last4"], p["date"], p["merchant"], p["txn_type"], p["source"]) == \
        (979.0, "9320", "2026-10-19", "AIRTEL PAYM", "debit", "cc")
    assert p["occurred_at"] == "2026-10-19T01:49:17+05:30"


def test_axis_older_wordings_still_parse():
    p = parse_single_sms("Spent INR 399\nAxis Bank Card no. XX9320\n06-10-26 19:24:59 IST\nAIRTEL PAYM\n"
                         "Avl Limit: INR 158906.62\nNot you? SMS BLOCK 9320 to 910000000001")
    assert (p["amount"], p["card_last4"], p["date"], p["merchant"]) == (399.0, "9320", "2026-10-06", "AIRTEL PAYM")
    assert p["occurred_at"] == "2026-10-06T19:24:59+05:30"
    p = parse_single_sms("Spent\nCard no. XX9320\nINR 799\n07-08-25 08:42:33\nAIRTEL PAYM\nAvl Lmt INR 159201\n"
                         "SMS BLOCK 9320 to 910000000001, if not you - Axis Bank")
    assert (p["amount"], p["card_last4"], p["date"], p["merchant"], p["bank"]) == \
        (799.0, "9320", "2025-08-07", "AIRTEL PAYM", "Axis")


# --- H5: bank-side refunds / reversals / cashback -------------------------------------------------

@pytest.mark.parametrize("body,exp", [
    ("Alert! Rs. 1511 refunded by SWIGGY E COM           BANGALORE     IND on 15/AUG/2026 & adjusted against "
     "HDFC Bank Credit Card 9434 View updated balance here: https://example.invalid/x",
     dict(amount=1511.0, card_last4="9434", date="2026-08-15", kind="refund", merchant="SWIGGY E COM BANGALORE IND")),
    ("Reversal of Rs 4.01 credited to ICICI Bank Credit Card XX0026 on 03-APR-26. Revised total due Rs 0, minimum due Rs .00",
     dict(amount=4.01, card_last4="0026", date="2026-04-03", kind="reversal")),
    ("Reversal of Rs 4.00 credited to your ICICI Bank Credit Card XX0026 on 13-MAR-26.",
     dict(amount=4.0, card_last4="0026", date="2026-03-13", kind="reversal")),
    ("AMAZON INDIA CYBS SI refund of Rs 1,499.00 credited to ICICI Bank Credit Card XX0026 on 25-MAY-26. "
     "Revised total due Rs 65,761.05, minimum due Rs 6,516.26",
     dict(amount=1499.0, card_last4="0026", date="2026-05-25", kind="refund", merchant="AMAZON INDIA CYBS SI")),
    ("Rs. 551 has been credited to your SBI Credit Card xxxx2111, towards reversal/cashback from SWIGGY FOOD   "
     "         BANGALORE     IN for trxn. dated 18/09/2026",
     dict(amount=551.0, card_last4="2111", date=None, kind="reversal", merchant="SWIGGY FOOD BANGALORE IN")),
    ("Dear Cardholder, Trxn. of Rs.75.00 on your SBI Credit Card ending 2111 at CANVAPAAAAQQ0QQQQ001 dated 18-07-26 "
     "has been cancelled and refunded by the merchant. For further details please call at 18601801290",
     dict(amount=75.0, card_last4="2111", date=None, kind="refund", merchant="CANVAPAAAAQQ0QQQQ001")),
    ("Congratulations! Cashback of INR 141 has been credited to your Airtel Axis Bank Credit Card XX9320 towards "
     "your spends during AUG'26.",
     dict(amount=141.0, card_last4="9320", date=None, kind="cashback")),
    ("It's Cashback Time! Happy to share that Rs. 714 has been credited to your CASHBACK SBI Card account for the "
     "month of Jul'26 - SBI Card.",
     dict(amount=714.0, card_last4="", date=None, kind="cashback")),
    ("HDFC Bank: Your UPI transaction of Rs. 280.00 has been reversed in your account due to technical problem "
     "(UPI Ref no. 500000000001)",
     dict(amount=280.0, card_last4="", date=None, kind="reversal", ref_no="500000000001")),
    ("HDFC Bank : Your UPI transaction of 2500.00 has been reversed in your account due to technical problem "
     "(UPI Ref no. 500000000002)",
     dict(amount=2500.0, kind="reversal", ref_no="500000000002")),
    ("ATM Txn Reversed:Rs.2300 reversed to your HDFC Bank Card 3605 At +MAIN ROAD COLONY On 2025-08-27:16:03:08."
     "Inconvenience regretted",
     dict(amount=2300.0, card_last4="3605", date="2025-08-27", kind="reversal", source="atm")),
])
def test_bank_side_credits_carry_card_date_and_kind(body, exp):
    p = parse_single_sms(body)
    assert p is not None and p["txn_type"] == "credit"
    for k, v in exp.items():
        assert p[k] == v, (k, p[k], v)


def test_sbi_cashback_card_is_resolved_later_not_hard_coded():
    p = parse_single_sms("It's Cashback Time! Happy to share that Rs. 1275 has been credited to your CASHBACK SBI "
                         "Card account for the month of Apr'26 - SBI Card.")
    assert p["card_lookup"] == "sbi_cashback" and p["card_last4"] == ""


# --- M5 / M6: SBI e-mandates, SBI savings, HSBC account debits ------------------------------------

def test_sbi_emandate_success_inr_and_usd():
    p = parse_single_sms(
        "Transaction Successful! Rs.310.00 has been debited to your SBI Credit Card XX2111 against recurring e-mandate "
        "registered by you at merchant site. Details: Merchant: RAZTrendlyne SiHub ID: Fake1234Ab Trxn. Ref. No.: "
        "300000000000001 Date: 07-Oct-2026. Time: 02:4")
    assert (p["amount"], p["card_last4"], p["merchant"], p["ref_no"], p["date"], p["currency"]) == \
        (310.0, "2111", "RAZTrendlyne", "300000000000001", "2026-10-07", "INR")
    assert p["occurred_at"] is None        # truncated time is not invented
    p = parse_single_sms(
        "Transaction Successful! Rs.199.00 has been debited to your SBI Credit Card XX2111 against recurring e-mandate "
        "registered by you at merchant site. Details: Merchant: NETFLIX SiHub ID: Fake5678Cd Trxn. Ref. No.: "
        "300000000000002 Date: 03-Oct-2026. Time: 16:07:43 ")
    assert p["occurred_at"] == "2026-10-03T16:07:43+05:30" and p["merchant"] == "NETFLIX"
    p = parse_single_sms(
        "Transaction Successful! USD118.00 has been debited to your SBI Credit Card XX2111 against recurring e-mandate "
        "registered by you at merchant site. Details: Merchant: ANTHROPICCLAUDESUB SiHub ID: Fake9012Ef Trxn. Ref. "
        "No.: 400000000000003 Date: 25-Sep-2026. Time")
    assert (p["currency"], p["original_amount"], p["fx_estimated"]) == ("USD", 118.0, 1)
    assert p["amount"] == to_inr_estimate("USD", 118.0) == round(118 * APPROX_INR_PER_UNIT["USD"], 2)
    assert p["date"] == "2026-09-25"


def test_sbi_emandate_debit_merchant_is_not_the_whole_clause():
    p = parse_single_sms(
        "Transaction of Rs.199.00 at NETFLIX against E-mandate (SiHub ID - Fake5678Cd) registered by you at merchant "
        "has been debited to your SBI Credit Card ending 2111 on 02-09-26. To manage E-mandate, click: "
        "http://example.invalid/e")
    assert (p["merchant"], p["amount"], p["card_last4"], p["date"]) == ("NETFLIX", 199.0, "2111", "2026-09-02")
    # M7: the same wording in USD used to be stored as Rs 20
    p = parse_single_sms(
        "Transaction of USD20.00 at CURSORAIPOWEREDIDE against E-mandate (SiHub ID - Fake0000Gh) registered by you at "
        "merchant has been debited to your SBI Credit Card ending 2111 on 21-12-25.")
    assert (p["currency"], p["original_amount"], p["fx_estimated"]) == ("USD", 20.0, 1) and p["amount"] > 1000


@pytest.mark.parametrize("body,exp", [
    ("Dear UPI user A/C X4321 debited by 645.00 on date 18Oct26 trf to M/S.APARNA CINEM Refno 200000000001 If not u? "
     "call-1800111109 for other services-18001234-SBI",
     dict(amount=645.0, card_last4="4321", date="2026-10-18", merchant="M/S.APARNA CINEM", ref_no="200000000001",
          source="upi")),
    ("Your A/C XXXXX004321 Debited INR 40,000.00 on 25/09/26 -Transferred to Mr. RAVI KUMAR. Avl Balance INR "
     "81,754.23-SBI",
     dict(amount=40000.0, card_last4="4321", date="2026-09-25", merchant="Mr. RAVI KUMAR", source="bank")),
    ("Dear Customer, Your a/c no. XXXXXXXX4321 is debited for Rs.450000.00 on 24-08-26 and a/c XXXXXXX706 credited "
     "(IMPS Ref no 200000000002). -SBI",
     dict(amount=450000.0, card_last4="4321", date="2026-08-24", ref_no="200000000002")),
    ("Your Ac Xx4321 debited Rs.50,000.00 for transfer to Asha Ac Xx7777 dt 12.10.26 Ref 600000033. If not done by "
     "you, call 1800111109. YONO SBI",
     dict(amount=50000.0, card_last4="4321", date="2026-10-12", ref_no="600000033")),
    ("HSBC: Dear Customer, your HSBC A/c 111-222***-001 has been debited with INR 28,200.00- on 29SEP as transfer "
     "towards account 111-222***-706 . Your avl Bal is 820,091.37 .",
     dict(amount=28200.0, card_last4="001", date="2026-09-29", merchant="transfer towards account 111-222***-706")),
    ("HSBC: Dear Customer, your HSBC A/c 111-222***-001 has been debited with INR 15.73- on 30SEP as DEBIT INTEREST . "
     "Your avl Bal is 820,075.64 .",
     dict(amount=15.73, card_last4="001", date="2026-09-30", merchant="DEBIT INTEREST")),
    ("HSBC: Dear Customer, your HSBC A/c 111-222***-001 has been debited with INR 75.64- on 13OCT as per your request . "
     "Your avl Bal is 670,000.00 .",
     dict(amount=75.64, card_last4="001", date="2026-10-13")),
    ("HSBC: A/c 111-222***-001 is debited with INR 150,000.00- on 12OCT with UTR HSBCN50000000000000001 as NEFT via "
     "Online to SBIN A/c ***4321 Asha SBI . Avl Bal is INR 670,075.64 . Report fraud 18002663456.",
     dict(amount=150000.0, card_last4="001", date="2026-10-12", ref_no="HSBCN50000000000000001")),
])
def test_savings_and_hsbc_account_debits(body, exp):
    p = parse_single_sms(body, reference_date=date(2026, 10, 19))
    assert p is not None and p["txn_type"] == "debit"
    for k, v in exp.items():
        assert p[k] == v, (k, p[k], v)


# --- M7: currency is captured wherever a wording can carry one ------------------------------------

@pytest.mark.parametrize("body,cur,orig", [
    ("Spent USD 12.99 On HDFC Bank Card 9434 At NETFLIX.COM On 2026-10-18:10:00:00.Not You?", "USD", 12.99),
    ("USD 71.76 spent using ICICI Bank Card XX8013 on 03-Mar-26 on PROTONMAIL. Avl Limit: INR 92,317.33.", "USD", 71.76),
    ("Transaction of EUR45.00 at BOOKING.COM AMSTERDAM on your SBI Credit Card ending 2111 on 18/10/26.", "EUR", 45.0),
    ("Spent USD 20.00 \nusing Axis Bank Credit Card no. XX9320 on 18-10-26 10:00:00 IST at \nOPENAI. \nAvl limit: INR 1.",
     "USD", 20.0),
])
def test_foreign_amounts_are_converted_and_flagged(body, cur, orig):
    p = parse_single_sms(body)
    assert (p["currency"], p["original_amount"], p["fx_estimated"]) == (cur, orig, 1)
    assert p["amount"] == to_inr_estimate(cur, orig)


def test_fx_table():
    assert to_inr_estimate("INR", 10) == 10.0
    assert to_inr_estimate("usd", 1) == APPROX_INR_PER_UNIT["USD"]
    assert to_inr_estimate("JPY", 100) is None          # never stored as rupees


# --- M4: reference numbers ------------------------------------------------------------------------

@pytest.mark.parametrize("body,ref", [
    ("HDFC Bank : Your UPI transaction of 2500.00 has been reversed in your account (UPI Ref no. 554600000001)", "554600000001"),
    ("Dear UPI user A/C X4321 debited by 645.00 on date 18Oct26 trf to X Refno 237600000002 If not u?", "237600000002"),
    ("a/c XXXXXXX706 credited (IMPS Ref no 623600000003). -SBI", "623600000003"),
    ("HSBC: A/c 111-222***-001 is debited with INR 1.00- on 12OCT with UTR HSBCN52000000000000004 as NEFT", "HSBCN52000000000000004"),
    ("Merchant: NETFLIX SiHub ID: Ab12 Trxn. Ref. No.: 396200000000005 Date: 03-Oct-2026.", "396200000000005"),
    ("Sent Rs.98.00\nFrom HDFC Bank A/C *6611\nTo SHOP\nOn 17/10/26\nRef 972600000006\nNot You?", "972600000006"),
    ("Credit Alert!\nRs.225.00 credited to HDFC Bank A/c XX6611 on 08-09-26 from VPA a@b (UPI 493100000007)\n", "493100000007"),
    ("Txn Rs.539.70\nOn HDFC Bank Card 4956\nAt shop@ptybl\nby UPI 109700000008\nOn 30-04\nNot You?", "109700000008"),
    ("INR 72.00 is paid from HSBC account XX554006 to SHOP on 19-Jul-25 with ref 975800000009.", "975800000009"),
    ("Thx for INB txn of Rs.50000 frm A/c X4321 ref# IID9400010 on 27JUL25.", "IID9400010"),
])
def test_reference_numbers(body, ref):
    assert extract_ref_no(body) == ref


# --- merchants that contain "on" / "." no longer end early ---------------------------------------

def test_merchant_words_that_look_like_separators():
    p = parse_single_sms("Spent Rs.500 On HDFC Bank Card 9434 At CAFE ON THE ROCKS On 2026-10-18:12:00:00.Not You?")
    assert p["merchant"] == "CAFE ON THE ROCKS" and p["date"] == "2026-10-18"
    p = parse_single_sms("INR 800.00 spent using ICICI Bank Card XX0026 on 18-Oct-26 on DR. AGARWALS EYE HOSPITAL. "
                         "Avl Limit: INR 3,15,528.37.")
    assert p["merchant"] == "DR. AGARWALS EYE HOSPITAL"


def test_hsbc_transfer_between_own_accounts_is_named_a_self_transfer():
    debit = ("HSBC: Dear Customer, your HSBC A/c 111-222***-001 has been debited with INR 28,200.00- on 29SEP as "
             "transfer towards account 111-222***-706 . Your avl Bal is 820,091.37 .")
    credit = ("HSBC: Dear Customer, your HSBC A/c 111-222***-706 has been credited with INR 28,200.00+ on 29SEP as "
              "transfer from A/c 111-222***-001 . Your available Bal is 30,979.02 .")
    for body in (debit, credit):
        assert parse_single_sms(body, date(2026, 10, 1))["description"].startswith("Self transfer")
    other = debit.replace("towards account 111-222***-706", "towards account 222-333***-706")
    assert not parse_single_sms(other, date(2026, 10, 1))["description"].startswith("Self transfer")


def test_pluxee_unpadded_time():
    p = parse_single_sms("Rs. 442.00 spent from Pluxee  Meal Card wallet, card no.xx9824 on 20-04-2025 1:21:12 at "
                         "SWIGGY . Avl bal Rs.2610.46. Not you call 18002106919")
    assert p["pattern"] == "pluxee_spend" and p["bank"] == "Pluxee" and p["card_last4"] == "9824"
    assert p["occurred_at"] == "2025-04-20T01:21:12+05:30"


def test_merchant_refund_notice_is_not_a_parser_credit():
    # the retired refund_generic pattern turned this into a credit with bank 'Unknown' and no card
    assert parse_single_sms("Refund of Rs 423.0 has been initiated for Swiggy order 248300000000001. Updated balance "
                            "should reflect in 4-7 days. https://example.invalid/r") is None


def test_merchant_html_entities_are_decoded():
    from modules.sms.parser import _clean_merchant
    assert _clean_merchant("+2ND CD AT MAIN RD&#39;S C") == "+2ND CD AT MAIN RD'S C"
