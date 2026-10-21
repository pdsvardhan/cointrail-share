"""
SMS Parser for Indian bank transaction messages.
Handles HDFC, HSBC, SBI, ICICI, Axis, Pluxee (and generic PhonePe/GPay) formats.

This is adapted from BOTB's services/pdf-parser/sms_parser.py

iter-18 (2026-10-20 audit) reworked the extraction contract:
  * every pattern uses named groups (amount, card, merchant, date, time, cur, ref) and declares the
    account context (`source`: cc / upi / bank / atm / emi / meal_card) plus, for credits that give
    money back, the `kind` (refund / reversal / cashback) — H5;
  * the currency is captured wherever a wording can carry one and foreign amounts are converted to an
    INR estimate (util.fx) instead of being stored as rupees — M7;
  * the bank reference (UPI ref / UTR / IMPS / e-mandate ref) is extracted so identity can rest on it
    instead of amount+date+merchant — M4;
  * year-less dates ("13OCT", "30-04") take their year from the SMS's own IST receive date, never
    from the server clock — H8/M8;
  * dates are matched as date-shaped tokens, so an "On"/"on" inside a merchant name ("CAFE ON THE
    ROCKS") can no longer end the merchant early.
The parser only reads text. Deciding whether a message is noise, a bill payment, a statement alert or
must be quarantined is modules.sms.processing's job.
"""
import html
import re
from datetime import date, datetime

from util.fx import normalize_currency, to_inr_estimate
from util.timez import infer_year, today_ist


def normalize_amount(amount_str):
    """Parse amount string like '1,234.56', '500/-', '75.64-' (HSBC debit) or '20,805.00+' to float."""
    if not amount_str:
        return None
    cleaned = amount_str.replace(",", "").replace(" ", "").strip()
    cleaned = re.sub(r"/-$", "", cleaned).rstrip("-+")
    try:
        return float(cleaned)
    except ValueError:
        return None


MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "SEPT": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4, "JUNE": 6, "JULY": 7, "AUGUST": 8,
    "SEPTEMBER": 9, "OCTOBER": 10, "NOVEMBER": 11, "DECEMBER": 12,
}


def _as_date(reference_date):
    if reference_date is None:
        return today_ist()
    if isinstance(reference_date, datetime):
        return reference_date.date()
    if isinstance(reference_date, date):
        return reference_date
    return date.fromisoformat(str(reference_date)[:10])


def _iso(year, month, day):
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except (TypeError, ValueError):
        return None


def _full_year(yr):
    yr = int(yr)
    if yr < 100:
        yr += 2000 if yr < 50 else 1900
    return yr


def normalize_date(date_str, reference_date=None):
    """Normalize the date formats Indian banks print to YYYY-MM-DD (None if not a real date).

    `reference_date` is the IST calendar date the SMS was received (date or ISO string). A date
    printed without a year ('13OCT', '30-04') takes the year that puts it at or before that day
    (util.timez.infer_year) — the server's clock is never consulted, so an HSBC '31DEC' alert
    processed on 2 January lands in the year it was sent (iter-18 H8/M8). Defaults to today (IST)."""
    if not date_str:
        return None
    s = str(date_str).strip()
    # Strip non-date suffixes (".Not" in "2025-02-07:20:37:12.Not", "_value Date ...") and the
    # punctuation a date is followed by in running text ("07-Oct-2026.", "18-10-26,", "05-Oct-26;").
    s = re.sub(r"[._](?=[A-Za-z]).*$", "", s)
    s = s.strip().rstrip(".,;:").strip()

    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[:T ]\d{2}:\d{2}(?::\d{2})?)?$", s)
    if m:
        return _iso(m.group(1), m.group(2), m.group(3))

    # DD/MM/YYYY, DD-MM-YY, DD.MM.YY
    m = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})$", s)
    if m:
        return _iso(_full_year(m.group(3)), m.group(2), m.group(1))

    # DD-MM / DD/MM — no year (HDFC "Txn ... On 30-04")
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})$", s)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return _iso(infer_year(month, day, _as_date(reference_date)), month, day)
        return None

    # DD Mon YY / DD-Mon-YYYY / DD/MON/YYYY / DDMonYY / DD Mon (13OCT, 18Oct26, 2 Nov 25, 07-Oct-2026)
    m = re.match(r"^(\d{1,2})[\s/-]*([A-Za-z]{3,9})[\s/-]*(\d{2}|\d{4})?$", s)
    if m:
        month = MONTHS.get(m.group(2).upper())
        if not month:
            return None
        day = int(m.group(1))
        if m.group(3):
            return _iso(_full_year(m.group(3)), month, day)
        return _iso(infer_year(month, day, _as_date(reference_date)), month, day)

    # [Wkd] Mon DD[,] YYYY — Pluxee "Sun Apr 20 2025", CRED "February 07, 2026"
    m = re.match(r"^(?:[A-Za-z]{3},?\s+)?([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})$", s)
    if m:
        month = MONTHS.get(m.group(1).upper())
        return _iso(m.group(3), month, m.group(2)) if month else None

    return None


# --- shared regex fragments ----------------------------------------------------------------------
# Amount: "1,23,456.78", "500", "4.01". Must start with a digit (".00" is a withheld amount, not 0).
AMT = r"(?P<amount>\d[\d,]*(?:\.\d+)?)"
# Rupee prefix in every spelling seen: "Rs.500", "Rs 500", "Rs. 1,500", "INR500", "₹500" (M8).
INR = r"(?:Rs\.?|INR|₹)\s*"
# Currency-capturing prefix for wordings that can carry a foreign amount (M7).
CUR = r"(?P<cur>Rs\.?|INR|₹|USD|EUR|GBP|AED|SGD)\s*"
# Dates as printed after "on": 2026-10-17[:22:52:32], 18/10/26, 18-10-2026, 18.10.26, 18-Oct-26,
# 15/AUG/2026, 18Oct26, 18 Oct 26, 13OCT, 30-04.
DATE = (r"(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}[/.-](?:\d{1,2}|[A-Za-z]{3})[/.-]\d{2,4}"
        r"|\d{1,2}\s?[A-Za-z]{3}\s?\d{2,4}|\d{1,2}[A-Za-z]{3}|\d{1,2}[/-]\d{1,2})")
# Pluxee prints unpadded fields ("1:21:12", "23:54:5"); _occurred_at pads them.
TIME = r"(?P<time>\d{1,2}[:.]\d{1,2}[:.]\d{1,2})"
# HDFC prints date and time glued: "On 2026-10-17:22:52:32"
HDFC_DT = r"(?P<date>\d{4}-\d{2}-\d{2})(?::(?P<time>\d{2}:\d{2}:\d{2}))?"


# SMS regex patterns for Indian banks. First match wins, so specific wordings come before the
# generic fallbacks at the end. `source` is the account context handed to the categorizer and to
# txn_store.derive_kind; `kind` (optional) is what the movement intrinsically is.
PATTERNS = [
    # ---------------------------------------------------------------- refunds / reversals / cashback
    # iter-18 H5: bank-side credits that give money back. Each carries the card and (where printed)
    # the posting date, so it nets against the right card's spend. Merchant-side notices ("Refund of
    # Rs X has been initiated for Swiggy order") are NOT here — they move no money and are noise.
    {
        # "Alert! Rs. 1511 refunded by SWIGGY E COM  BANGALORE IND on 15/AUG/2026 & adjusted against
        # HDFC Bank Credit Card 9434"
        "name": "hdfc_cc_refund",
        "regex": INR + AMT + r"\s+refunded\s+by\s+(?P<merchant>.+?)\s+on\s+" + DATE
                 + r"\s*&\s*adjusted\s+against\s+HDFC\s+Bank\s+Credit\s+Card\s+x*(?P<card>\d{4})",
        "bank": "HDFC", "txn_type": "credit", "source": "cc", "kind": "refund",
    },
    {
        # "Reversal of Rs 4.01 credited to [your ]ICICI Bank Credit Card XX0026 on 03-APR-26."
        "name": "icici_cc_reversal",
        "regex": r"Reversal\s+of\s+" + INR + AMT + r"\s+credited\s+to\s+(?:your\s+)?ICICI\s+Bank\s+Credit\s+Card\s+"
                 r"XX(?P<card>\d{4})\s+on\s+" + DATE,
        "bank": "ICICI", "txn_type": "credit", "source": "cc", "kind": "reversal",
    },
    {
        # "AMAZON INDIA CYBS SI refund of Rs 1,499.00 credited to ICICI Bank Credit Card XX0026 on 25-MAY-26"
        # "refund of INR 250.00 from AMAZON PAY INDIA has been credited to your ICICI Bank Credit Card ..."
        "name": "icici_cc_refund",
        "regex": r"(?:(?P<merchant>[A-Z][A-Za-z0-9&*.' -]{1,40}?)\s+)?refund\s+of\s+" + INR + AMT
                 + r"\s+(?:from\s+(?P<merchant_alt>.+?)\s+)?(?:has\s+been\s+)?credited\s+to\s+(?:your\s+)?"
                 r"ICICI\s+Bank\s+Credit\s+Card\s+XX(?P<card>\d{4})\s+on\s+" + DATE,
        "bank": "ICICI", "txn_type": "credit", "source": "cc", "kind": "refund",
    },
    {
        # "Rs. 551 has been credited to your SBI Credit Card xxxx2111, towards reversal/cashback from
        # SWIGGY FOOD BANGALORE IN for trxn. dated 18/09/2026". The printed date is the ORIGINAL
        # purchase's; the credit happened when the SMS arrived, so no date group (receive date wins).
        "name": "sbi_cc_reversal",
        "regex": INR + AMT + r"\s+has\s+been\s+credited\s+to\s+your\s+SBI\s+Credit\s+Card\s+x*(?P<card>\d{4}),?\s+"
                 r"towards\s+reversal/cashback\s+from\s+(?P<merchant>.+?)\s+for\s+trxn",
        "bank": "SBI", "txn_type": "credit", "source": "cc", "kind": "reversal",
    },
    {
        # "Trxn. of Rs.75.00 on your SBI Credit Card ending 2111 at CANVA... dated 18-07-26 has been
        # cancelled and refunded by the merchant" — "dated" is the original purchase, receive date wins.
        "name": "sbi_cc_cancel_refund",
        "regex": r"Trxn\.?\s+of\s+" + CUR + AMT + r"\s+on\s+your\s+SBI\s+Credit\s+Card\s+ending\s+(?:XX)?(?P<card>\d{4})\s+"
                 r"at\s+(?P<merchant>.+?)\s+dated\s+\S+\s+has\s+been\s+cancelled\s+and\s+refunded",
        "bank": "SBI", "txn_type": "credit", "source": "cc", "kind": "refund",
    },
    {
        # "Congratulations! Cashback of INR 141 has been credited to your Airtel Axis Bank Credit Card
        # XX9320 towards your spends during AUG'26."
        "name": "axis_cc_cashback",
        "regex": r"Cashback\s+of\s+" + INR + AMT + r"\s+has\s+been\s+credited\s+to\s+your\s+.{0,40}?"
                 r"Axis\s+Bank\s+Credit\s+Card\s+XX(?P<card>\d{4})",
        "bank": "Axis", "txn_type": "credit", "source": "cc", "kind": "cashback",
        "description": "Axis Bank cashback",
    },
    {
        # "It's Cashback Time! Happy to share that Rs. 714 has been credited to your CASHBACK SBI Card
        # account for the month of Jul'26". No card number: processing resolves the CASHBACK SBI card
        # from the cards table (`card_lookup`), never a hard-coded last4.
        "name": "sbi_cashback",
        "regex": INR + AMT + r"\s+has\s+been\s+credited\s+to\s+your\s+CASHBACK\s+SBI\s+Card\s+account",
        "bank": "SBI", "txn_type": "credit", "source": "cc", "kind": "cashback",
        "card_lookup": "sbi_cashback", "description": "SBI Card cashback",
    },
    {
        # "HDFC Bank : Your UPI transaction of [Rs. ]280.00 has been reversed in your account due to
        # technical problem (UPI Ref no. 600000000015)" — no account number is printed.
        "name": "hdfc_upi_reversal",
        "regex": r"HDFC\s+Bank\s*:\s*Your\s+UPI\s+transaction\s+of\s+(?:" + INR + r")?" + AMT
                 + r"\s+has\s+been\s+reversed\s+in\s+your\s+account",
        "bank": "HDFC", "txn_type": "credit", "source": "upi", "kind": "reversal",
        "description": "HDFC UPI reversal",
    },
    {
        # "ATM Txn Reversed:Rs.2300 reversed to your HDFC Bank Card 3605 At +MAIN ROAD COLONY On 2025-09-05:12:38:35"
        "name": "hdfc_atm_reversal",
        "regex": r"ATM\s+Txn\s+Reversed\s*:\s*" + INR + AMT + r"\s+reversed\s+to\s+your\s+HDFC\s+Bank\s+Card\s+x*(?P<card>\d{4})"
                 r"\s+At\s+(?P<merchant>.+?)\s+On\s+" + HDFC_DT,
        "bank": "HDFC", "txn_type": "credit", "source": "atm", "kind": "reversal",
    },
    {
        # "Your Pluxee Card xx9824 has been credited with INR 499.00 on Sun Apr 20 2025 01:08:33as a
        # reversal against a previous transaction"
        "name": "pluxee_reversal",
        "regex": r"Your\s+Pluxee\s+Card\s+x*(?P<card>\d{4})\s+has\s+been\s+credited\s+with\s+" + INR + AMT
                 + r"\s+on\s+(?P<date>[A-Za-z]{3}\s+[A-Za-z]{3}\s+\d{1,2}\s+\d{4})\s*" + TIME + r"?\s*as\s+a\s+reversal",
        "bank": "Pluxee", "txn_type": "credit", "source": "meal_card", "kind": "reversal",
        "description": "Pluxee reversal",
    },
    {
        # "Your Pluxee Card has been successfully credited with Rs.3300 towards  Meal Wallet on Mon Mar
        # 24 2025 05:51:17" — the employer's monthly load: income, not money back on a card.
        "name": "pluxee_wallet_credit",
        "regex": r"Your\s+Pluxee\s+Card\s+has\s+been\s+successfully\s+credited\s+with\s+" + INR + AMT
                 + r"\s+towards\s+(?P<merchant>.+?)\s+on\s+(?P<date>[A-Za-z]{3}\s+[A-Za-z]{3}\s+\d{1,2}\s+\d{4})\s*" + TIME + "?",
        "bank": "Pluxee", "txn_type": "credit", "source": "meal_card", "kind": "transfer_in",
    },

    # ---------------------------------------------------------------- Axis card spends
    {
        # iter-18 H6: Axis reworded its spend alert on 2026-10-16 —
        # "Spent INR 979 \nusing Axis Bank Credit Card no. XX9320 on 19-10-26 01:49:17 IST at \nAIRTEL PAYM. \nAvl limit: ..."
        "name": "axis_spent_using",
        "regex": r"Spent\s+" + CUR + AMT + r"\s+using\s+Axis\s+Bank\s+Credit\s+Card\s+no\.?\s*XX(?P<card>\d{4})\s+on\s+"
                 r"(?P<date>\d{1,2}-\d{1,2}-\d{2,4})\s+" + TIME + r"\s*(?:IST)?\s+at\s+(?P<merchant>.+?)\s*\.?\s*(?:\n|Avl|$)",
        "bank": "Axis", "txn_type": "debit", "source": "cc",
    },
    {
        # "Spent INR 399\nAxis Bank Card no. XX9320\n06-10-26 19:24:59 IST\nAIRTEL PAYM\nAvl Limit: ..." (to 2026-10)
        "name": "axis_spent_multiline",
        "regex": r"Spent\s+" + CUR + AMT + r"\s*\n?\s*Axis\s+Bank\s+Card\s+no\.?\s*XX(?P<card>\d{4})\s*\n?\s*"
                 r"(?P<date>\d{1,2}-\d{1,2}-\d{2,4})\s+" + TIME + r"\s+IST\s*\n?\s*(?P<merchant>.+?)(?:\s*\n|\s+Avl)",
        "bank": "Axis", "txn_type": "debit", "source": "cc",
    },
    {
        # "Spent\nCard no. XX9320\nINR 799\n07-08-25 08:42:33\nAIRTEL PAYM\nAvl Lmt INR 159201\n... - Axis Bank" (2025)
        "name": "axis_spent_card_first",
        "regex": r"(?=[\s\S]*Axis\s+Bank)Spent\s*\n\s*Card\s+no\.?\s*XX(?P<card>\d{4})\s*\n\s*" + CUR + AMT
                 + r"\s*\n\s*(?P<date>\d{1,2}-\d{1,2}-\d{2,4})\s+" + TIME + r"\s*\n\s*(?P<merchant>[^\n]+?)\s*\n",
        "bank": "Axis", "txn_type": "debit", "source": "cc",
    },

    # ---------------------------------------------------------------- HDFC
    {
        # "Spent Rs.932 On HDFC Bank Card 9434 At RAZ*Swiggy On 2026-10-17:22:52:32.Not You?"
        "name": "hdfc_spent_on",
        "regex": r"Spent\s+" + CUR + AMT + r"\s+On\s+HDFC\s+Bank\s+(?:Credit\s+)?Card\s+x*(?P<card>\d{4})\s+At\s+"
                 r"(?P<merchant>.+?)\s+On\s+" + HDFC_DT,
        "bank": "HDFC", "txn_type": "debit", "source": "cc",
    },
    {
        # "Rs.1145 spent on HDFC Bank Card x9434 at NYKAA E RETAIL LIMI on 2026-07-17:18:44:22.Not U?"
        "name": "hdfc_cc_spend",
        "regex": CUR + AMT + r"\s+spent\s+on\s+HDFC\s+Bank\s+(?:Credit\s+)?Card\s+x*(?P<card>\d{4})\s+(?:at|via)\s+"
                 r"(?P<merchant>.+?)\s+on\s+" + HDFC_DT,
        "bank": "HDFC", "txn_type": "debit", "source": "cc",
    },
    {
        # "Rs.199 without OTP/PIN HDFC Bank Card x7180 At NETFLIX On 2025-03-02:23:03:14"
        "name": "hdfc_without_otp",
        "regex": CUR + AMT + r"\s+without\s+OTP/PIN\s+HDFC\s+Bank\s+Card\s+x*(?P<card>\d{4})\s+At\s+(?P<merchant>.+?)\s+On\s+" + HDFC_DT,
        "bank": "HDFC", "txn_type": "debit", "source": "cc",
    },
    {
        # RuPay credit card via UPI: "Txn Rs.539.70\nOn HDFC Bank Card 4956\nAt dominospizzaonline@ptybl\n
        # by UPI 600000000012\nOn 30-04" — the date has no year.
        "name": "hdfc_txn_multiline",
        "regex": r"Txn\s+" + INR + AMT + r"\s*\n?\s*On\s+HDFC\s+Bank\s+Card\s+x*(?P<card>\d{4})\s*\n?\s*At\s+(?P<merchant>.+?)\s*\n?\s*"
                 r"(?:by\s+UPI\s+(?P<ref>\d+)\s*\n?\s*)?On\s+" + DATE,
        "bank": "HDFC", "txn_type": "debit", "source": "cc",
    },
    {
        # pipe-delimited variant of the above
        "name": "hdfc_pipe_upi",
        "regex": r"Txn\s+" + INR + AMT + r"\s*\|\s*On\s+HDFC.*?(?P<card>\d{4})\s*\|\s*At\s+(?P<merchant>.+?)\s*\|.*?On\s+" + DATE,
        "bank": "HDFC", "txn_type": "debit", "source": "upi",
    },
    {
        # "Sent Rs.98.00\nFrom HDFC Bank A/C *6611\nTo VIJETHA SUPER MARKETS PVT\nOn 17/10/26\nRef 600000000027"
        # The date line MUST be anchored to a newline: patterns compile with IGNORECASE, so a bare "On"
        # also matches the "on" inside a merchant name ("Naturals Sal|on").
        "name": "hdfc_sent_multiline",
        "regex": r"Sent\s+" + INR + AMT + r"\s*\n?\s*From\s+HDFC\s+Bank\s+A/C\s+[*x]*(?P<card>\d{4})\s*\n?\s*To\s+"
                 r"(?P<merchant>[^\n]+?)\s*\n\s*On\s+" + DATE,
        "bank": "HDFC", "txn_type": "debit", "source": "upi",
    },
    {
        # "UPI Mandate:\nSent Rs.1.00\nfrom HDFC Bank A/c 6611\nTo MOHALLA TECH PRIVATE L\n03/09/26\nRef 600000000026"
        "name": "hdfc_upi_mandate_sent",
        "regex": r"UPI\s+Mandate:\s*\n?\s*Sent\s+" + INR + AMT + r"\s*\n?\s*from\s+HDFC\s+Bank\s+A/c\s+x*(?P<card>\d{4})\s*\n?\s*"
                 r"To\s+(?P<merchant>[^\n]+?)\s*\n\s*" + DATE,
        # 'upi_mandate', not 'upi': only a merchant can hold a UPI AutoPay mandate, so the payee is
        # never a person (iter-203 E4; categorizer.is_merchant_payee). Kind stays 'debit'.
        "bank": "HDFC", "txn_type": "debit", "source": "upi_mandate",
    },
    {
        # "Credit Alert!\nRs.225.00 credited to HDFC Bank A/c XX6611 on 08-09-26 from VPA instamart.refund@axisbank (UPI 600000000020)"
        "name": "hdfc_credit_alert",
        "regex": INR + AMT + r"\s+credited\s+to\s+HDFC\s+Bank\s+A/c\s*x*(?P<card>\d{4})\s+on\s+" + DATE
                 + r"\s+from\s+(?:VPA\s+)?(?P<merchant>.+?)(?:\s*\(UPI|\s+Avl|$)",
        "bank": "HDFC", "txn_type": "credit", "source": "upi",
    },
    {
        # "Received!\nINR 1,00,000.00 in HDFC Bank A/c xx6611\nOn 06-12-25\nFor IMPS -<sender>- 600000000023"
        "name": "hdfc_imps_received",
        "regex": r"Received!\s*\n?\s*" + INR + AMT + r"\s+in\s+HDFC\s+Bank\s+A/c\s+x*(?P<card>\d{4})\s*\n?\s*On\s+" + DATE
                 + r"\s*\n?\s*For\s+(?P<merchant>[^\n]+?)\s*(?:\n|Avl|$)",
        "bank": "HDFC", "txn_type": "credit", "source": "bank",
    },
    {
        "name": "hdfc_debit_ac",
        "regex": INR + AMT + r"\s+debited\s+from\s+a/c\s+\*+(?P<card>\d{4})\s+on\s+" + DATE
                 + r"\s+(?:to\s+VPA\s+)?(?P<merchant>.+?)(?:\s*\(UPI|\.\s|$)",
        "bank": "HDFC", "txn_type": "debit", "source": "bank",
    },
    {
        # "Withdrawn Rs.2500 From HDFC Bank Card x3605 At +2ND CD AT MAIN RD'S C On 2025-09-26:14:00:46"
        "name": "hdfc_withdrawn",
        "regex": r"Withdrawn\s+" + INR + AMT + r"\s+From\s+HDFC\s+Bank\s+Card\s+x*(?P<card>\d{4})\s+At\s+(?P<merchant>.+?)\s+On\s+" + HDFC_DT,
        "bank": "HDFC", "txn_type": "debit", "source": "atm",
    },
    {
        # "UPI ATM Withdrawal:Rs.2200.\nFrom:HDFC Bank A/c 6611\nAt:HDFC ATM ATM\nUPI RRN:600000000024 on 31/07/26"
        "name": "hdfc_upi_atm",
        "regex": r"UPI\s+ATM\s+Withdrawal\s*:\s*" + INR + AMT + r"\.?\s*\n?\s*From\s*:\s*HDFC\s+Bank\s+A/c\s+x*(?P<card>\d{4})\s*\n?\s*"
                 r"At\s*:\s*(?P<merchant>.+?)\s*\n?\s*UPI\s+RRN\s*:\s*(?P<ref>\d+)\s+on\s+" + DATE,
        "bank": "HDFC", "txn_type": "debit", "source": "atm",
    },
    {
        # "Amt Deducted!\nRs.42,000.00 from your HDFC Bank A/c XX6611 for NEFT transaction via HDFC Bank
        # Online Banking\nNot you?..." — no date printed (receive date).
        "name": "hdfc_amt_deducted",
        "regex": r"Amt\s+Deducted!\s*\n?\s*" + INR + AMT + r"\s+from\s+your\s+HDFC\s+Bank\s+A/c\s+X*(?P<card>\d{4})\s+for\s+"
                 r"(?P<merchant>[^\n]+?)\s*(?:\n|Not\s+you|$)",
        "bank": "HDFC", "txn_type": "debit", "source": "bank",
    },
    {
        # "PAYMENT ALERT! \nINR 2500.00 deducted from HDFC Bank A/C No 6611 towards Groww UMRN: ..."
        "name": "hdfc_emi_deduct",
        "regex": INR + AMT + r"\s+deducted\s+from\s+HDFC\s+Bank\s+A/C\s+No\s+(?P<card>\d{4})\s+towards\s+(?P<merchant>.+?)\s+UMRN",
        "bank": "HDFC", "txn_type": "debit", "source": "emi",
    },

    # ---------------------------------------------------------------- HSBC
    {
        # "Your HSBC credit card xxxxx8938 is used at raz*sanwaria texpro pr for INR 29045.10 on 05/07/25."
        # "HSBC creditcard xxxxx8938 used at myntra designs pvt ltd for INR 11757.00 on 18/02/26.Limit Rs ..."
        "name": "hsbc_cc_used",
        "regex": r"HSBC\s+credit\s*card\s+x*(?P<card>\d{4})\s+(?:is\s+)?used\s+at\s+(?P<merchant>.+?)\s+for\s+" + CUR + AMT
                 + r"\s+on\s+" + DATE,
        "bank": "HSBC", "txn_type": "debit", "source": "cc",
    },
    {
        # "INR 72.00 is paid from HSBC account XX554006 to <payee> on 19-Jul-25 with ref 600000000028."
        "name": "hsbc_upi_paid",
        "regex": r"INR\s+" + AMT + r"\s+is\s+(?:paid|debited)\s+from\s+HSBC\s+account\s+X*\d*?(?P<card>\d{4})\s+to\s+(?P<merchant>.+?)\s+on\s+" + DATE,
        "bank": "HSBC", "txn_type": "debit", "source": "upi",
    },
    {
        # "Your HSBC Acc XXXXXX4006 is debited for INR 255.00 on 31-Jul-25 towards GROFERS INDIA ... UPI Ref No ..."
        "name": "hsbc_debited_for",
        "regex": r"HSBC\s+Acc\s+X*\d*?(?P<card>\d{4})\s+is\s+debited\s+for\s+INR\s+" + AMT + r"\s+on\s+" + DATE
                 + r"\s+towards\s+(?P<merchant>.+?)(?:\.\s|\.$|\s+UPI)",
        "bank": "HSBC", "txn_type": "debit", "source": "upi",
    },
    {
        # "Your HSBC Acc XXXXXX4006 is credited for INR 2.00 on 17-Jul-25 from googlepay@axisbank. UPI Ref No ..."
        "name": "hsbc_credited_for",
        "regex": r"HSBC\s+Acc\s+X*\d*?(?P<card>\d{4})\s+is\s+credited\s+for\s+INR\s+" + AMT + r"\s+on\s+" + DATE
                 + r"\s+from\s+(?P<merchant>.+?)(?:\.\s|\.$|\s+UPI)",
        "bank": "HSBC", "txn_type": "credit", "source": "upi",
    },
    {
        # iter-18 M6: "HSBC: Dear Customer, your HSBC A/c 111-222***-001 has been debited with INR 28,200.00- on
        # 29SEP as transfer towards account 111-222***-706 ." / "... as DEBIT INTEREST ." / "... as per your request ."
        # The printed account suffix (3 digits) is the account identity; the date has no year.
        "name": "hsbc_ac_debited_with",
        "regex": r"HSBC\s+A/c\s+[\dX*-]*?(?P<card>\d{3,4})\s+has\s+been\s+debited\s+with\s+INR\s+" + AMT + r"-?\s+on\s+" + DATE
                 + r"\s+as\s+(?P<merchant>.+?)\s*\.\s*Your\s+avl",
        "bank": "HSBC", "txn_type": "debit", "source": "bank",
    },
    {
        # "HSBC: A/c 111-222***-001 is debited with INR 150,000.00- on 12OCT with UTR HSBCN5... as NEFT via
        # Online to SBIN A/c ***4321 <name> . Avl Bal is ..."
        "name": "hsbc_ac_debited_utr",
        "regex": r"HSBC:\s*(?:Your\s+)?A/c\s+[\dX*-]*?(?P<card>\d{3,4})\s+is\s+debited\s+with\s+INR\s+" + AMT + r"-?\s+on\s+" + DATE
                 + r"(?:\s+at\s+" + TIME + r")?\s+with\s+UTR\s+(?P<ref>\w+)\s+as\s+(?P<merchant>.+?)\s*\.\s*Avl",
        "bank": "HSBC", "txn_type": "debit", "source": "bank",
    },
    {
        # "your HSBC A/c 111-222***-706 has been credited with INR 20,805.00+ on 26DEC as SALARY CREDIT ."
        "name": "hsbc_credited_with",
        "regex": r"HSBC\s+A/c\s+[\dX*-]*?(?P<card>\d{3,4})\s+has\s+been\s+credited\s+with\s+INR\s+" + AMT + r"\+?\s+on\s+" + DATE
                 + r"(?:\s+as\s+(?P<merchant>.+?)\s*\.\s*Your)?",
        # money coming INTO the account (salary, interest, own transfers): stated, so a description such as
        # "SALARY CREDIT" can never be read as a refund or a card-bill payment
        "bank": "HSBC", "txn_type": "credit", "source": "bank", "kind": "transfer_in",
    },
    {
        # "HSBC: A/c 111-222***-706 is credited with INR 417,000.00+ on 10DEC at 14.05.40 with UTR SBINN5... as NEFT from ..."
        "name": "hsbc_ac_credited_utr",
        "regex": r"HSBC:\s*(?:Your\s+)?A/c\s+[\dX*-]*?(?P<card>\d{3,4})\s+is\s+credited\s+with\s+INR\s+" + AMT + r"\+?\s+on\s+" + DATE
                 + r"(?:\s+at\s+" + TIME + r")?\s+with\s+UTR\s+(?P<ref>\w+)\s+as\s+(?P<merchant>.+?)\s*\.\s*Your",
        "bank": "HSBC", "txn_type": "credit", "source": "bank", "kind": "transfer_in",
    },
    {
        # "Dear Customer, Your HSBC account has been successfully debited with 2.00 on 18/07/2025 towards
        # Google Play UPI AutoPay, <mandate id>." — no currency token, no account number.
        "name": "hsbc_autopay_debit",
        "regex": r"Your\s+HSBC\s+account\s+has\s+been\s+successfully\s+debited\s+with\s+(?:" + INR + r")?" + AMT
                 + r"\s+on\s+" + DATE + r"\s+towards\s+(?P<merchant>.+?)\s+UPI\s+AutoPay",
        "bank": "HSBC", "txn_type": "debit", "source": "upi",
    },
    {
        "name": "hsbc_generic_credit",
        "regex": r"INR\s+" + AMT + r"\s+(?:is\s+)?credited\s+(?:to|in)\s+(?:your\s+)?HSBC\s+(?:account\s*)?(?:XX)?(?P<card>\d{4,6}).*?on\s+" + DATE,
        "bank": "HSBC", "txn_type": "credit", "source": "bank",
    },
    {
        "name": "hsbc_debit",
        "regex": r"INR\s+" + AMT + r"\s+(?:is\s+)?debited\s+from\s+(?:your\s+)?HSBC\s+(?:account\s*)?(?:XX)?(?P<card>\d{4,6}).*?on\s+" + DATE,
        "bank": "HSBC", "txn_type": "debit", "source": "bank",
    },

    # ---------------------------------------------------------------- SBI card
    {
        # iter-18 M5: "Transaction Successful! Rs.310.00 has been debited to your SBI Credit Card XX2111 against
        # recurring e-mandate ... Details: Merchant: RAZTrendlyne SiHub ID: ... Trxn. Ref. No.: 600000000000005
        # Date: 07-Oct-2026. Time: 02:4..." and the USD variant "Transaction Successful! USD118.00 ...".
        # The SMS is often truncated mid-time, so the time is taken only when complete.
        "name": "sbi_emandate_success",
        "regex": r"Transaction\s+Successful!\s*" + CUR + AMT + r"\s+has\s+been\s+debited\s+to\s+your\s+SBI\s+Credit\s+Card\s+"
                 r"(?:XX|ending\s+)?(?P<card>\d{4}).*?Merchant:\s*(?P<merchant>.+?)\s+SiHub\s+ID.*?Date:\s*" + DATE
                 + r"(?:\.?\s*Time:\s*" + TIME + r")?",
        "bank": "SBI", "txn_type": "debit", "source": "cc",
    },
    {
        # iter-18 M5: "Transaction of Rs.199.00 at NETFLIX against E-mandate (SiHub ID - ...) registered by you at
        # merchant has been debited to your SBI Credit Card ending 2111 on 02-09-26." The merchant is the
        # word(s) before "against", not the whole clause up to "debited".
        "name": "sbi_emandate_debit",
        "regex": r"Transaction\s+of\s+" + CUR + AMT + r"\s+at\s+(?P<merchant>.+?)\s+against\s+E-?mandate.*?debited\s+to\s+your\s+"
                 r"SBI\s+Credit\s+Card\s+ending\s+(?P<card>\d{4})\s+on\s+" + DATE,
        "bank": "SBI", "txn_type": "debit", "source": "cc",
    },
    {
        # "Transaction of EUR45.00 at BOOKING.COM on your SBI Credit Card ending 2111 on 18/10/26." and the
        # older "Transaction of Rs.X at M has been debited ... card ending N on DATE". Replaces
        # sbi_card_foreign, which stored USD 20 as Rs 20 (M7).
        "name": "sbi_card_spend",
        "regex": r"Transaction\s+of\s+" + CUR + AMT + r"\s+at\s+(?P<merchant>.+?)\s+(?:(?:has\s+been\s+)?debited\s+(?:to|from)|on)\s+"
                 r"your\s+SBI\s+Credit\s+Card\s+ending\s+(?P<card>\d{4})\s+on\s+" + DATE,
        "bank": "SBI", "txn_type": "debit", "source": "cc",
    },
    {
        # "Rs.287.00 spent on your SBI Credit Card ending 2111 at BLINKIT on 19/10/26."
        "name": "sbi_card_spent",
        "regex": CUR + AMT + r"(?:/-)?\s+spent\s+on\s+(?:your\s+)?SBI\s+Credit\s+Card\s+ending\s+(?P<card>\d{4})\s+at\s+(?P<merchant>.+?)\s+on\s+" + DATE,
        "bank": "SBI", "txn_type": "debit", "source": "cc",
    },
    {
        "name": "sbi_card_debit_v3",
        "regex": CUR + AMT + r"\s+(?:has\s+been\s+)?debited\s+from\s+(?:your\s+)?SBI\s+(?:Credit\s+)?Card\s+ending\s+(?P<card>\d{4})"
                 r"(?:\s+at\s+(?P<merchant>.+?))?\s+on\s+" + DATE,
        "bank": "SBI", "txn_type": "debit", "source": "cc",
    },

    # ---------------------------------------------------------------- SBI savings account (M6)
    {
        # "Dear UPI user A/C X4321 debited by 645.00 on date 18Oct26 trf to M/S.APARNA CINEM Refno 200000000009"
        # — no "Rs" before the amount.
        "name": "sbi_upi_debit",
        "regex": r"UPI\s+user\s+A/C\s+X*(?P<card>\d{4})\s+debited\s+by\s+(?:" + INR + r")?" + AMT + r"\s+on\s+date\s+" + DATE
                 + r"\s+trf\s+to\s+(?P<merchant>.+?)\s+Ref\s*no\.?\s*(?P<ref>\d+)",
        "bank": "SBI", "txn_type": "debit", "source": "upi",
    },
    {
        # "Your A/C XXXXX004321 Debited INR 40,000.00 on 25/09/26 -Transferred to Mr. <name>. Avl Balance INR ..."
        "name": "sbi_ac_debited_transfer",
        "regex": r"Your\s+A/C\s+X*\d*?(?P<card>\d{4})\s+Debited\s+" + INR + AMT + r"\s+on\s+" + DATE
                 + r"\s*-?\s*Transferred\s+to\s+(?P<merchant>.+?)\.\s*Avl",
        "bank": "SBI", "txn_type": "debit", "source": "bank",
    },
    {
        # "Your a/c no. XXXXXXXX4321 is debited for Rs.450000.00 on 24-08-26 and a/c XXXXXXX706 credited
        # (IMPS Ref no 600000000025)."
        "name": "sbi_imps_debit",
        "regex": r"Your\s+a/c\s+no\.?\s*X*\d*?(?P<card>\d{4})\s+is\s+debited\s+for\s+" + INR + AMT + r"\s+on\s+" + DATE
                 + r"\s+and\s+(?P<merchant>a/c\s+X*\d+)\s+credited",
        "bank": "SBI", "txn_type": "debit", "source": "bank",
        # the counterparty is only an account number: name the rail so the categorizer's IMPS keyword
        # files it as a transfer, not as an Uncategorized (= everyday) spend of lakhs
        "describe": "IMPS to {merchant}",
    },
    {
        # YONO: "Your Ac x4321 debited Rs.1,00,000.00 for transfer to <name> Ac x6611 dt 19.05.26 Ref 200000000010."
        "name": "sbi_yono_transfer",
        # the payee's account suffix is dropped from the merchant so a person's name reads as one
        "regex": r"Your\s+Ac\s+X*(?P<card>\d{4})\s+debited\s+" + INR + AMT + r"\s+for\s+transfer\s+to\s+(?P<merchant>.+?)"
                 r"(?:\s+Ac\s+X*\d+)?\s+dt\s+" + DATE,
        "bank": "SBI", "txn_type": "debit", "source": "bank",
    },
    {
        # YONO standing instruction: "SBI Ac X4321 debited for SI Fund Transfer of Rs.45,000.00 to . Ac X0022 on 24.02.26 Ref ..."
        "name": "sbi_si_transfer",
        "regex": r"SBI\s+Ac\s+X*(?P<card>\d{4})\s+debited\s+for\s+SI\s+Fund\s+Transfer\s+of\s+" + INR + AMT
                 + r"\s+to\s+(?P<merchant>.+?)\s+on\s+" + DATE,
        "bank": "SBI", "txn_type": "debit", "source": "bank",
        "description": "SBI SI fund transfer",
    },
    {
        # "Standing Instruction has been successfully executed for Rs. 40,000.00 from A/c No.XXXXX004321 to
        # Loan A/c No.XXXXX300022 on 25/08/26."
        "name": "sbi_si_executed",
        "regex": r"Standing\s+Instruction\s+has\s+been\s+successfully\s+executed\s+for\s+" + INR + AMT
                 + r"\s+from\s+A/c\s+No\.?\s*X*\d*?(?P<card>\d{4})\s+to\s+(?P<merchant>.+?)\s+on\s+" + DATE,
        "bank": "SBI", "txn_type": "debit", "source": "bank",
        "describe": "Standing instruction to {merchant}",
    },
    {
        # "Your A/c XX4321 has been debited with INR 6,00,000.00 on 09/12/2025 towards RTGS with UTR SBINR1...
        # sent to <name> <IFSC>-SBI"
        "name": "sbi_rtgs_debit",
        "regex": r"Your\s+A/c\s+X*(?P<card>\d{4})\s+has\s+been\s+debited\s+with\s+" + INR + AMT + r"\s+on\s+" + DATE
                 + r"\s+towards\s+(?:RTGS|NEFT|IMPS)\s+with\s+UTR\s+(?P<ref>\w+)\s+sent\s+to\s+(?P<merchant>.+?)(?:\s+[A-Z]{4}0[A-Z0-9]{6})?\s*-\s*SBI",
        "bank": "SBI", "txn_type": "debit", "source": "bank",
    },
    {
        # "Your A/C ending with 4321 has been debited for INR 236.0 on 11-12-25 towards annual maintenance
        # charges for your SBI Debit Card ending with 1058"
        "name": "sbi_ac_debited_towards",
        "regex": r"Your\s+A/C\s+ending\s+with\s+(?P<card>\d{4})\s+has\s+been\s+debited\s+for\s+" + INR + AMT + r"\s+on\s+" + DATE
                 + r"\s+towards\s+(?P<merchant>.+?)(?:\s+for\s+your|\.|$)",
        "bank": "SBI", "txn_type": "debit", "source": "bank",
    },
    {
        # "Thx for INB txn of Rs.50000 frm A/c X4321 ref# IID9400099 on 27JUL25." /
        # "... frm A/c X4321 to CFMS_AND. Ref IK0000000X on 29Nov25."
        "name": "sbi_inb_txn",
        "regex": r"Thx\s+for\s+INB\s+txn\s+of\s+" + INR + AMT + r"\s+frm\s+A/c\s+X*(?P<card>\d{4})\s+(?:to\s+(?P<merchant>.+?)\.\s+)?"
                 r"Ref#?\s*(?P<ref>\w+)\s+on\s+" + DATE,
        "bank": "SBI", "txn_type": "debit", "source": "bank",
        "description": "SBI internet banking transfer",
    },
    {
        # "Your loan A/C XXXXX300011 Credited INR 75,000.00 on 22/07/26,deposit transfer from Mr. <name>.-SBI"
        "name": "sbi_loan_credited",
        "regex": r"Your\s+loan\s+A/C\s+X*\d*?(?P<card>\d{4})\s+Credited\s+" + INR + AMT + r"\s+on\s+" + DATE
                 + r",?\s*(?P<merchant>.+?)\.?\s*-\s*SBI",
        "bank": "SBI", "txn_type": "credit", "source": "bank",
    },
    {
        # "Your Loan XXXXX300011 debited by Rs 118.00 on 03/01/26"
        "name": "sbi_loan_debit",
        "regex": r"Loan\s+X*\d*?(?P<card>\d{4})\s+debited\s+by\s+" + INR + AMT + r"\s+on\s+" + DATE,
        "bank": "SBI", "txn_type": "debit", "source": "emi",
        "description": "SBI loan debit",
    },
    {
        # "An IT Refund amount of Rs 112990 for AY-2025-26 has been credited to your account XXXXXXXXXX6611
        # on 2025-10-22." Sent by SBI (the refund banker) about whichever account received it — so the bank
        # is not SBI. A tax refund is income, not money back on a purchase.
        "name": "it_refund_credit",
        "regex": r"IT\s+Refund\s+amount\s+of\s+" + INR + AMT + r"\s+for\s+AY[\d-]*\s+has\s+been\s+credited\s+to\s+your\s+"
                 r"account\s+X*(?P<card>\d{4})\s+on\s+" + DATE,
        "bank": "Unknown", "txn_type": "credit", "source": "bank", "kind": "transfer_in",
        "description": "Income tax refund",
    },

    # ---------------------------------------------------------------- ICICI
    {
        # "INR 203.84 spent using ICICI Bank Card XX0026 on 29-Sep-26 on USHODAYA SUPER . Avl Limit: ..."
        # "USD 71.76 spent using ICICI Bank Card XX8013 on 03-Mar-26 on PROTONMAIL."
        "name": "icici_spent_using",
        "regex": CUR + AMT + r"\s+spent\s+using\s+ICICI\s+Bank\s+Card\s+XX(?P<card>\d{4})\s+on\s+" + DATE
                 + r"\s+on\s+(?P<merchant>.+?)(?:\s*\.\s*Avl|\s+Avl|\s*\.\s*If\s+not|\s*\.?\s*$)",
        "bank": "ICICI", "txn_type": "debit", "source": "cc",
    },
    {
        # "Rs 9,648.00 spent on ICICI Bank Card XX0026 on 08-Sep-26 at ZUDIO. Avl Lmt: ..."
        "name": "icici_cc_spend",
        "regex": CUR + AMT + r"\s+spent\s+on\s+ICICI\s+Bank\s+Card\s+XX(?P<card>\d{4})\s+on\s+" + DATE
                 + r"\s+at\s+(?P<merchant>.+?)(?:\s*\.\s*Avl|\s+Avl|\s*\.?\s*$)",
        "bank": "ICICI", "txn_type": "debit", "source": "cc",
    },
    {
        # Card standing instruction debit: "We have successfully processed payment of INR 1499.00 to Merchant
        # Amazon, as per Standing Instruction X on 24/05/2026 for ICICI Bank Credit Card 0026."
        "name": "icici_si_debit",
        "regex": r"processed\s+payment\s+of\s+" + CUR + AMT + r"\s+to\s+Merchant\s+(?P<merchant>.+?),?\s+as\s+per\s+Standing\s+"
                 r"Instruction\s+\w+\s+on\s+" + DATE + r"\s+for\s+ICICI\s+Bank\s+Credit\s+Card\s+(?:XX)?(?P<card>\d{4})",
        "bank": "ICICI", "txn_type": "debit", "source": "cc",
    },
    {
        "name": "icici_debit_v2",
        "regex": r"ICICI\s+Bank\s+(?:Acct?|Account)\s*X*(?P<card>\d{4})\s+is\s+debited\s+with\s+" + INR + AMT + r"\s+on\s+" + DATE
                 + r"(?:\s+(?:to|for|Info:?)\s*(?P<merchant>.+?))?(?:\.|$)",
        "bank": "ICICI", "txn_type": "debit", "source": "bank",
    },
    {
        "name": "icici_credit",
        "regex": r"ICICI\s+Bank\s+(?:Acct?|Account)\s*X*(?P<card>\d{4})\s+is\s+credited\s+with\s+" + INR + AMT + r"\s+on\s+" + DATE,
        "bank": "ICICI", "txn_type": "credit", "source": "bank",
    },
    {
        # ICICI savings: "ICICI Bank Acct XX1234 debited for Rs 240.00 on 05-Oct-26; Swiggy credited. UPI:..."
        "name": "icici_debit",
        "regex": r"ICICI\s+Bank\s+(?:Acct|Account)\s*X*(?P<card>\d{3,4})\s+debited\s+for\s+" + INR + AMT + r"\s+on\s+" + DATE
                 + r";?\s+(?P<merchant>.+?)\s+credited",
        "bank": "ICICI", "txn_type": "debit", "source": "bank",
    },

    # ---------------------------------------------------------------- Pluxee meal card
    {
        # "Rs. 141.00 spent from Pluxee  Meal Card wallet, card no.xx9824 on 13-05-2025 13:20:34 at COMPASS . Avl bal ..."
        # (older bodies print "card no. on ..." with no number)
        "name": "pluxee_spend",
        "regex": INR + AMT + r"\s+spent\s+from\s+Pluxee.*?card\s+no\.?\s*(?:x*(?P<card>\d{4}))?\s+on\s+" + DATE
                 + r"\s+" + TIME + r"\s+at\s+(?P<merchant>.+?)(?:\s*\.\s*Avl|\s*\.|$)",
        "bank": "Pluxee", "txn_type": "debit", "source": "meal_card",
    },
    {
        # "Rs.9.46 deducted from your Pluxee Card xx9824 towards Inactivity Fee."
        "name": "pluxee_fee",
        "regex": INR + AMT + r"\s+deducted\s+from\s+your\s+Pluxee\s+Card\s+x*(?P<card>\d{4})\s+towards\s+(?P<merchant>.+?)(?:\.|$)",
        "bank": "Pluxee", "txn_type": "debit", "source": "meal_card", "kind": "fee",
    },

    # ---------------------------------------------------------------- generic fallbacks
    {
        "name": "axis_debit",
        "regex": INR + AMT + r".*?(?:debited|spent).*?(?:Acct|A/c|account)\s*(?:no\.?\s*)?X*(?P<card>\d{4}).*?(?:on|at)\s+" + DATE,
        "bank": "Axis", "txn_type": "debit", "source": "bank",
    },
    {
        "name": "phonepe_debit",
        "regex": r"(?:Paid|Sent)\s+" + INR + AMT + r"\s+to\s+(?P<merchant>.+?)(?:\s+on\s+" + DATE + r")?(?:\.|$)",
        "bank": "PhonePe", "txn_type": "debit", "source": "upi",
    },
    {
        "name": "gpay_debit",
        "regex": r"You\s+(?:paid|sent)\s+" + INR + AMT + r"\s+to\s+(?P<merchant>.+?)(?:\s+on\s+" + DATE + r")?(?:\.|$)",
        "bank": "GPay", "txn_type": "debit", "source": "upi",
    },
    {
        "name": "generic_debit",
        "regex": INR + AMT + r"\s+(?:has\s+been\s+)?(?:debited|spent|paid|withdrawn).*?(?:a/c|acct|account|card)\s*(?:no\.?\s*)?"
                 r"(?:XX|x|ending\s*)?(?P<card>\d{4})",
        "bank": "Unknown", "txn_type": "debit", "source": "bank",
    },
    {
        "name": "generic_credit",
        "regex": INR + AMT + r"\s+(?:has\s+been\s+)?(?:credited|received|deposited).*?(?:a/c|acct|account)\s*(?:no\.?\s*)?(?:XX|x)?(?P<card>\d{4})",
        "bank": "Unknown", "txn_type": "credit", "source": "bank",
    },
]

for _p in PATTERNS:
    _p["compiled"] = re.compile(_p["regex"], re.IGNORECASE | re.DOTALL)


# Bank reference numbers (iter-18 M4). Ordered: the most specific wording first. The value is the
# stable identity modules.merge links on, so it is normalised (no spaces, upper case).
_REF_PATTERNS = [
    re.compile(r"\bTrxn\.?\s*Ref\.?\s*No\.?\s*:?\s*([A-Z0-9]{6,})", re.I),          # SBI e-mandate
    re.compile(r"\bUPI\s*Ref\.?\s*(?:No\.?|number)?\s*[:.]?\s*(\d{6,})", re.I),      # HSBC / HDFC reversal
    re.compile(r"\(UPI\s+(\d{6,})\)", re.I),                                          # HDFC credit alert
    re.compile(r"\bby\s+UPI\s+(\d{6,})", re.I),                                       # HDFC RuPay-on-UPI
    re.compile(r"\bUPI\s*RRN\s*:?\s*(\d{6,})", re.I),                                 # HDFC UPI ATM
    re.compile(r"\bIMPS\s+Ref\.?\s*no\.?\s*:?\s*(\d{6,})", re.I),                     # SBI IMPS
    re.compile(r"\bUTR\s*(?:No\.?)?\s*[:.]?\s*([A-Z0-9]{10,})", re.I),                # NEFT / RTGS
    re.compile(r"\bwith\s+ref\s+(\d{6,})", re.I),                                     # HSBC "is paid ... with ref"
    re.compile(r"\bRef\s*no\.?\s*(\d{6,})", re.I),                                    # SBI UPI "Refno"
    re.compile(r"\bIMPS\s*-[^\n]*?-\s*(\d{9,})", re.I),                               # HDFC "For IMPS -<name>- 5340..."
    re.compile(r"\bRef(?:#|\b)\s*:?\s*([A-Z]{0,6}\d{6,}|[A-Z0-9]{8,})", re.I),        # HDFC Sent / YONO / INB
    re.compile(r"\bUPI\s*:\s*(\d{6,})", re.I),                                        # ICICI savings
]


def extract_ref_no(body):
    """The bank reference (UPI ref / RRN / IMPS ref / UTR / e-mandate ref) a message carries, or None."""
    for rx in _REF_PATTERNS:
        m = rx.search(body or "")
        if m:
            return m.group(1).strip().upper()
    return None


def _clean_merchant(text):
    if not text:
        return ""
    # SMS Backup & Restore stores bodies HTML-escaped ("MAIN RD&#39;S C"); a merchant is plain text
    t = " ".join(html.unescape(str(text)).split())
    return t.strip(" .,;:-")


_HSBC_OWN_AC = re.compile(r"HSBC\s+A/c\s+(\d{3}-\d{3}\*+-)\d{3,4}\s", re.I)
_HSBC_OTHER_AC = re.compile(r"transfer\s+(?:towards\s+account|from\s+A/c)\s+(\d{3}-\d{3}\*+-)\d{3,4}\b", re.I)


def _is_own_account_transfer(pattern_name, body, merchant):
    """HSBC "debited ... as transfer towards account 111-222***-706" / "credited ... as transfer from A/c
    111-222***-001": both accounts carry the same customer prefix, so this is the owner moving money
    between their own HSBC accounts — neither spend nor income (iter-18 M6)."""
    if pattern_name not in ("hsbc_ac_debited_with", "hsbc_credited_with"):
        return False
    own, other = _HSBC_OWN_AC.search(body or ""), _HSBC_OTHER_AC.search(merchant or "")
    return bool(own and other and own.group(1) == other.group(1))


def _occurred_at(date_iso, time_str):
    """Full IST timestamp when the message printed a time with its date (txn.occurred_at)."""
    if not (date_iso and time_str):
        return None
    parts = re.split(r"[:.]", time_str)
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    hh, mm, ss = (int(x) for x in parts)
    if hh > 23 or mm > 59 or ss > 59:
        return None
    return f"{date_iso}T{hh:02d}:{mm:02d}:{ss:02d}+05:30"


def parse_single_sms(body, reference_date=None):
    """Parse a single SMS body into a transaction dict or None.

    `reference_date` is the IST date the SMS was received; it resolves dates printed without a year.
    The returned `amount` is always INR — for a foreign-currency spend it is the util.fx estimate with
    `currency`, `original_amount` and `fx_estimated=1` alongside (iter-18 M7). `date` is None when the
    message prints none (the caller dates it by its receive time)."""
    if not body or not body.strip():
        return None

    for pattern in PATTERNS:
        m = pattern["compiled"].search(body)
        if not m:
            continue
        g = m.groupdict()
        amount = normalize_amount(g.get("amount"))
        if not amount or amount <= 0:
            continue

        currency = normalize_currency(g.get("cur"))
        original_amount = None
        fx_estimated = 0
        if currency != "INR":
            original_amount = amount
            amount = to_inr_estimate(currency, original_amount)
            fx_estimated = 1

        date_raw = g.get("date")
        date_iso = normalize_date(date_raw, reference_date) if date_raw else None
        merchant = ""
        for key in ("merchant", "merchant_alt"):
            if g.get(key) and g[key].strip():
                merchant = _clean_merchant(g[key])
                break
        if pattern["name"] == "hdfc_imps_received":
            # "IMPS -<sender>- 600000000023": the trailing number is the reference, not the payer
            merchant = _clean_merchant(re.sub(r"-?\s*\d{9,}\s*$", "", merchant))
        card = g.get("card") or ""
        kind = pattern.get("kind")
        if not kind and pattern["txn_type"] == "debit" and re.search(r"\bcharges?\b", merchant, re.I):
            kind = "fee"                       # "towards annual maintenance charges"
        description = merchant or pattern.get("description") or f"{pattern['bank']} {kind or pattern['txn_type']}"
        if merchant and pattern.get("describe"):
            description = pattern["describe"].format(merchant=merchant)
        if pattern["txn_type"] == "debit" and re.search(r"\bLoan\s+A/c\b", merchant, re.I):
            # a repayment into a loan account: the categorizer files "EMI" wording under EMI & Loans
            description = f"Loan repayment (EMI) - {description}"
        if _is_own_account_transfer(pattern["name"], body, merchant):
            # Name the movement so the categorizer's "self transfer" alias files it as Self Transfer
            # instead of everyday spend / money sent to people.
            description = f"Self transfer ({merchant})"

        return {
            "pattern": pattern["name"],
            "bank": pattern["bank"],
            "amount": amount,
            "currency": currency,
            "original_amount": original_amount,
            "fx_estimated": fx_estimated,
            "card_last4": card[-4:] if card else "",
            "date": date_iso,
            "occurred_at": _occurred_at(date_iso, g.get("time")),
            "txn_type": pattern["txn_type"],
            "kind": kind,
            "merchant": merchant,
            "description": description,
            "source": pattern["source"],
            "ref_no": (g.get("ref") or "").strip().upper() or extract_ref_no(body),
            "card_lookup": pattern.get("card_lookup"),
            "raw_text": body,
        }

    return None


def parse_sms_batch(messages, reference_date=None):
    """Parse a list of SMS body strings. Returns list of parsed transactions."""
    results = []
    for msg in messages:
        parsed = parse_single_sms(msg, reference_date)
        if parsed:
            results.append(parsed)
    return results
