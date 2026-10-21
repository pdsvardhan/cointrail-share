"""
Bank transaction-alert emails as a transaction source — iter-18 H4.

SBI sends an SMS for only a minority of card spends, but every spend gets a "Transaction Alert"
email. Emails used to be enrichment-only, so from 2026-09-19 to the 2026-10-19 audit 33 spends on
SBI 2111 (Rs 15,004) existed only as emails and Cointrail showed Rs 3,094 unbilled where CRED
showed Rs 15,802.63. Every bank alert email is now an observation like an SMS: it is written
through txn_store.insert_transaction(source='email', source_ref=<emails.id>), which links it to
an SMS or statement-line copy of the same purchase immediately (the email always loses, so a
purchase seen three times still counts once).

Two things are specific to email:
  * one money movement can produce TWO alert emails from different templates — SBI sends a
    "Transaction Alert" and a "Recurring E-mandate debit success" for every e-mandate debit, and
    YONO + CBSSBI both report the same savings-account transfer. merge.py never pairs two rows of
    the same source (that is what keeps genuine repeat purchases apart), so those "twins" are
    linked here: same card, same amount, different alert template, sent within TWIN_WINDOW.
  * old emails (synced before body extraction existed) only kept Gmail's ~200-char snippet. A
    truncated alert that lacks the card or amount is counted as skipped, not quarantined: that
    history is covered by the statement PDFs, and quarantining it would bury real new wordings.

Anything from an alert sender that names an amount and a movement but matches no template is
quarantined (modules.quarantine), never dropped.
"""
import html
import re
import sqlite3
from datetime import date as _date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from modules import merge, quarantine
from modules.txn_store import insert_transaction
from util.timez import IST, today_ist

# sender -> bank. The sender is the authority: a bank's alert mailbox only sends alerts, OTPs and
# account notices, so each one is scanned whatever is_financial says (pre-iter-18 the classifier
# missed *.bank.in and alerts.sbi.co.in, and those emails were stored as non-financial).
ALERT_SENDERS = {
    "onlinesbicard@sbicard.com": "SBI",
    "alerts@hdfcbank.net": "HDFC",
    "alerts@hdfcbank.bank.in": "HDFC",
    "credit_cards@icicibank.com": "ICICI",
    "credit_cards@icici.bank.in": "ICICI",
    "alerts@axis.bank.in": "Axis",
    "alerts@axisbank.com": "Axis",
    # SBI savings / debit card
    "cbsalerts.sbi@alerts.sbi.co.in": "SBI",
    "cbsalerts.sbi@alerts.sbi.bank.in": "SBI",
    "yonobysbi@alerts.sbi.co.in": "SBI",
    "yonobysbi@alerts.sbi.bank.in": "SBI",
    "neftinfo.itps@alerts.sbi.co.in": "SBI",
    "neftinfo.itps@alerts.sbi.bank.in": "SBI",
    "donotreply.sbiatm@alerts.sbi.co.in": "SBI",
    "donotreply.sbiatm@alerts.sbi.bank.in": "SBI",
}

HOURLY_LOOKBACK_DAYS = 14       # email_sync looks back 7 days; a margin for a late sync
TWIN_WINDOW = timedelta(minutes=10)   # one movement's two alert mails arrive ~20 s apart; 30 min apart
                                      # is another transfer (iter-18 verifier: 43 of 59 probe double counts)
SNIPPET_LEN = 260               # Gmail snippets are ~200 chars; a body this short may be cut off
SIBLING_ID_SPAN = 60            # emails of one sync batch sit close together by id

_CUR = r"(?P<cur>Rs\.?|INR|₹|USD|EUR|GBP|AED|SGD)"
_AMT = r"\s*\.?\s*(?P<amt>[\d,]*\.?\d+)"
_MONEY = _CUR + _AMT
_CARD = r"(?P<card>\d{4})"
_TIME = r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?)"
_D_NUM = r"\d{1,2}[/.-]\d{1,2}[/.-](?:\d{4}|\d{2})"               # 19/10/26, 17-10-26, 12.10.26
_D_DMY = r"\d{1,2}[ -][A-Za-z]{3,9},?[ -](?:\d{4}|\d{2})"          # 17 Oct, 2026 / 25 Sep 26 / 07-Oct-2026
_D_MDY = r"[A-Za-z]{3,9} \d{1,2},? \d{4}"                          # Apr 09, 2026 / April 03, 2026
_DATE = rf"(?P<date>{_D_NUM}|{_D_DMY}|{_D_MDY})"
_VPA = r"(?P<vpa>[\w.*+-]+@[\w.-]+)"

# (name, bank, regex, fields). Order matters only where wordings overlap; first match wins.
# `kind` sets txn_type/context/pattern_kind: card_debit, card_credit_reversal, upi_debit,
# upi_credit, bank_debit, bank_credit, bank_credit_reversal, bank_fee, atm.
TEMPLATES = [
    # ---- SBI Card (onlinesbicard@sbicard.com) -------------------------------------------------
    ("sbi_card_spend", "SBI",
     rf"{_MONEY} spent on your SBI Credit Card ending {_CARD}(?: at (?P<merchant>.+?) on {_DATE})?",
     "card_debit"),
    ("sbi_card_no_afa", "SBI",
     rf"Trxn\.? of {_MONEY} done on your credit card ending {_CARD}"
     rf"(?:.*?authentication mandated by RBI,? at (?P<merchant>.+?) on {_DATE})?",
     "card_debit"),
    ("sbi_emandate_debit", "SBI",
     rf"Transaction of {_MONEY} at (?P<merchant>.+?) against E-?mandate"
     rf"(?:.*?debited to your SBI Credit Card ending {_CARD})?(?: on {_DATE})?",
     "card_debit"),
    ("sbi_emandate_processed", "SBI",
     rf"processed on your SBI Credit Card XX ending with {_CARD}.*?Amount: {_MONEY} Merchant: (?P<merchant>.+?)"
     rf" SiHub ID:.*?(?:Trxn\.? Ref\.? No\.?: (?P<ref>\w+) )?Date: {_DATE}(?: Time: {_TIME})?",
     "card_debit"),
    # ---- HDFC Bank (alerts@hdfcbank.net / alerts@hdfcbank.bank.in) ----------------------------
    ("hdfc_cc_debit", "HDFC",
     rf"{_MONEY} (?:has been|is) debited from your HDFC Bank Credit Card ending {_CARD} towards "
     rf"(?P<merchant>.+?) on {_DATE}(?: at {_TIME})?",
     "card_debit"),
    ("hdfc_cc_auth", "HDFC",
     rf"Credit Card ending in {_CARD} ?\.? ?You made a transaction of {_MONEY} at (?P<merchant>.+?) on {_DATE}"
     rf"(?:,? {_TIME})?(?: ?\.)?(?: Authorization code: (?P<ref>\w+))?",
     "card_debit"),
    ("hdfc_rupay_upi", "HDFC",
     rf"{_MONEY} has been debited from your HDFC Bank RuPay Credit Card XX{_CARD} to {_VPA} (?P<merchant>.+?) "
     rf"on {_DATE}\.? ?(?:Your UPI transaction reference number is (?P<ref>\d+))?",
     "card_debit"),
    ("hdfc_upi_debit", "HDFC",
     rf"{_MONEY} is debited from your account ending {_CARD} towards VPA {_VPA}(?: \((?P<merchant>[^)]*)\))? "
     rf"on {_DATE}\.?(?: UPI transaction reference no\.?:? (?P<ref>\d+))?",
     "upi_debit"),
    ("hdfc_upi_debit_v1", "HDFC",
     rf"{_MONEY} has been debited from account (?:ending )?{_CARD} to "
     rf"(?:VPA {_VPA} (?P<merchant>.+?)|account (?P<dest>[*X\d]+)) on {_DATE}\.? ?"
     rf"(?:Your UPI transaction reference number is (?P<ref>\d+))?",
     "upi_debit"),
    ("hdfc_upi_credit", "HDFC",
     rf"{_MONEY} is successfully credited to your account \*\*{_CARD} by VPA {_VPA} (?P<merchant>.+?) on {_DATE}\.? ?"
     rf"(?:Your UPI transaction reference number is (?P<ref>\d+))?",
     "upi_credit"),
    ("hdfc_account_credit", "HDFC",
     rf"{_MONEY} has been successfully credited to your HDFC Bank account ending in {_CARD}\.?"
     rf"(?: Transaction Details: a\. Date: {_DATE})?"
     rf"(?: b\. Sender: (?P<merchant>.+?)(?: \(VPA: {_VPA}\))? c\. (?:UPI )?Reference No\.?:? (?P<ref>\w+))?",
     "upi_credit"),
    ("hdfc_account_transfer", "HDFC",
     rf"{_MONEY} has been deducted from your HDFC Bank account ending in X*{_CARD} for a transfer to payee "
     rf"(?P<merchant>.+?) via (?:NEFT|IMPS|RTGS)",
     "bank_debit"),
    ("hdfc_atm", "HDFC",
     rf"Debit Card ending {_CARD} for ATM withdrawal for {_MONEY} in (?P<city>.+?) at (?P<merchant>.+?) on {_DATE}"
     rf"(?: {_TIME})?",
     "atm"),
    # ---- ICICI Bank (credit_cards@icicibank.com / credit_cards@icici.bank.in) -----------------
    ("icici_cc_txn", "ICICI",
     rf"ICICI Bank Credit Card XX{_CARD} has been used for a transaction of {_MONEY} on {_DATE}"
     rf"(?: at {_TIME})?\.?(?: Info: (?P<merchant>.+?)(?:\. The Available|\.? ?$|\. ))?",
     "card_debit"),
    ("icici_cc_reversal", "ICICI",
     rf"reversal of {_MONEY} has been done on your ICICI Bank Credit Card XX{_CARD} on {_DATE}",
     "card_credit_reversal"),
    # ---- Axis Bank (alerts@axis.bank.in) ------------------------------------------------------
    ("axis_cc_spend", "Axis",
     rf"Transaction Amount: {_MONEY} Merchant Name: (?P<merchant>.+?) Axis Bank Credit Card"
     rf"(?: No\.? XX{_CARD})?(?: Date & Time: {_DATE},? {_TIME})?",
     "card_debit"),
    # ---- SBI savings account (CBSSBI / YONO / NEFT / ATM) -------------------------------------
    ("sbi_cbs_debit", "SBI",
     rf"Your A/?C X+\d*?{_CARD} Debited {_MONEY} on {_DATE} ?-(?P<merchant>.+?)\. Avl Bal",
     "bank_debit"),
    ("sbi_cbs_credit", "SBI",
     rf"Your A/?C X+\d*?{_CARD} Credited {_MONEY} on {_DATE} ?-(?P<merchant>.+?)\. Avl Bal",
     "bank_credit"),
    ("sbi_cbs_debit_transfer", "SBI",
     rf"Your A/?C X+\d*?{_CARD} has a debit by transfer of {_MONEY} on {_DATE}",
     "bank_debit"),
    ("sbi_cbs_credit_transfer", "SBI",
     rf"Your A/?C X+\d*?{_CARD} has a credit by Transfer of {_MONEY} on {_DATE}(?: by (?P<merchant>[^.]+?))?\.",
     "bank_credit"),
    ("sbi_cbs_cash", "SBI",
     rf"Your A/?C X+\d*?{_CARD} has a Cash withdrawal of {_MONEY} on {_DATE}",
     "atm"),
    ("sbi_yono_transfer", "SBI",
     rf"SBI Ac x{_CARD} debited {_MONEY} for transfer to (?P<merchant>.+?) Ac x\d+ dt {_DATE}(?: Ref (?P<ref>\w+))?",
     "bank_debit"),
    ("sbi_yono_si", "SBI",
     rf"SBI Ac x{_CARD} debited for SI Fund Transfer of {_MONEY} to (?P<merchant>.*?) ?Ac x(?P<dest>\d+) on {_DATE}"
     rf"(?: Ref (?P<ref>\w+))?",
     "bank_debit"),
    ("sbi_yono_ft", "SBI",
     rf"Ref\. No\. is (?P<ref>\w+) for transaction of {_MONEY} from A/c ending {_CARD} to (?P<merchant>.+?) on {_DATE}"
     rf"(?: at {_TIME})?",
     "bank_debit"),
    ("sbi_yono_success", "SBI",
     rf"Transaction Status Successful Amount {_MONEY} Transaction Number (?P<ref>\w+) Date of Transaction {_DATE}"
     rf" Debit account x{_CARD} Beneficiary Name (?P<merchant>.+?) Beneficiary Account",
     "bank_debit"),
    ("sbi_neft_in", "SBI",
     rf"credited for NEFT received.*?Credited to Your A/c: X*{_CARD} Amount: {_MONEY}(?: UTR No\.?: (?P<ref>\w+))?"
     rf"(?: Date: {_DATE})?(?: Sent by: (?P<merchant>.+?)(?: Sender Bank|$))?",
     "bank_credit"),
    ("sbi_neft_out", "SBI",
     rf"NEFT transaction originated by you are given below\.? A/c Debited: X*{_CARD} Date: {_DATE} "
     rf"UTR No\.?: (?P<ref>\w+) Beneficiary Name: (?P<merchant>.+?) Beneficiary A/c.*?Amount Remitted: {_MONEY}",
     "bank_debit"),
    # every field optional: emails synced before body extraction kept only the first ~200 chars,
    # and those must be recognised (as incomplete) rather than quarantined as an unknown wording.
    ("sbi_debit_card", "SBI",
     r"The below transaction has been done using your SBI debit card\.?"
     r"(?:.*?Terminal Owner Name (?P<merchant>.+?) (?=Date|Transaction|Amount|Last|$))?"
     rf"(?:.*?Date (?:&|and) Time {_DATE},? {_TIME})?"
     r"(?:.*?Amount \(INR\) (?P<amt>[\d,]*\.?\d+))?"
     rf"(?:.*?Last 4 Digit of Account X*{_CARD})?"
     r"(?:.*?Transaction Type (?P<ttype>[A-Za-z ]+?) Channel)?"
     r"(?:.*?Location (?P<loc>.+?) City)?",
     "atm"),
]
_COMPILED = [(n, b, re.compile(rx, re.I), k) for n, b, rx, k in TEMPLATES]

_KIND_MAP = {
    # kind: (txn_type, context, pattern_kind)
    "card_debit": ("debit", "email-card", None),
    "card_credit_reversal": ("credit", "email-card", "reversal"),
    "upi_debit": ("debit", "upi", None),
    "upi_credit": ("credit", "upi", None),
    "bank_debit": ("debit", "bank", None),
    "bank_credit": ("credit", "bank", None),
    "atm": ("debit", "atm", "atm"),
}

# Messages from alert senders that mention money but record no completed money movement.
# Checked BEFORE the templates (a declined attempt must never become a spend) ...
_NEGATED = re.compile(
    r"\bdeclined\b|\bunsuccessful\b|has failed|failed due to|\bfailure!|transaction failed|"
    r"was not completed|could not be processed", re.I)
# ... and AFTER them (alert footers say "Never share your Card Number, CVV, PIN, OTP").
_NOISE = re.compile(
    r"one time password|\bOTP\b (?:for|is)|pre-?debit alert|is due on|will be processed|"
    r"registration success|has been registered|received payment|payment received|"
    r"we have received payment|e-?statement|statement for the period|converted the purchase|"
    r"equated monthly instal|emi due|irregular by|loan of rs|loan a/?c|loan account|interest rate|"
    r"intt rate|si has been set up|has been closed|collateral|transaction done today at sbi|"
    r"credited to beneficiary|cancelled as requested|beneficiary addition|beneficiary .{0,40}activated|"
    r"cooling period|mpin|logged in|login|biometric|registered your device|password|usage settings|"
    r"modified card usage|missed call|relationship manager|credit balance|reward point", re.I)
# Subjects that announce a transaction: such an email that no template reads is quarantined.
_ALERT_SUBJECT = re.compile(
    r"transaction alert|transaction success|spent on|debited|credited|upi txn|payment was made|"
    r"noticed a transaction|e-?mandate debit|processed successfully|neft transaction|reversal processed",
    re.I)

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def _normalise(text):
    t = html.unescape(text or "")
    t = re.sub(r"[\xa0\u200b-\u200f\ufeff]", " ", t)        # nbsp, zero-width chars, BOM
    return re.sub(r"\s+", " ", t).strip()


def _money(cur, amt):
    """('Rs.', '1,457.00') -> ('INR', 1457.0); a missing or zero amount -> (code, None)."""
    c = (cur or "INR").upper().rstrip(".")
    code = "INR" if c in ("RS", "INR", "₹") else c
    try:
        value = float((amt or "").replace(",", ""))
    except ValueError:
        return code, None
    return code, (round(value, 2) if value > 0 else None)


def _year(y):
    y = int(y)
    return y + 2000 if y < 100 else y


def parse_body_date(s):
    """A date printed in an alert body -> datetime.date, or None. Indian banks print day first."""
    s = (s or "").strip().rstrip(".,")
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), _year(m.group(3))
    else:
        m = re.fullmatch(r"(\d{1,2})[ -]([A-Za-z]{3,9}),?[ -](\d{4}|\d{2})", s)
        if m:
            d, mo, y = int(m.group(1)), _MONTHS.get(m.group(2)[:3].lower()), _year(m.group(3))
        else:
            m = re.fullmatch(r"([A-Za-z]{3,9}) (\d{1,2}),? (\d{4})", s)
            if not m:
                return None
            d, mo, y = int(m.group(2)), _MONTHS.get(m.group(1)[:3].lower()), int(m.group(3))
    if not mo:
        return None
    try:
        return _date(y, mo, d)
    except ValueError:
        return None


def header_datetime(date_header):
    """The email Date header as an aware datetime (naive headers are UTC), or None."""
    if not date_header:
        return None
    try:
        dt = parsedate_to_datetime(date_header)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def header_ist_date(date_header):
    """IST calendar date (YYYY-MM-DD) of an email Date header — 'Sat, 18 Oct 2026 19:00:00 +0000'
    is 19 Oct in India (iter-18 M8: never decide a calendar date in UTC)."""
    dt = header_datetime(date_header)
    return dt.astimezone(IST).date().isoformat() if dt else None


def _clean_merchant(m):
    m = re.sub(r"\s+", " ", m or "").strip(" .,-:")
    return m[:80] or None


def _recognise(sender_email, subject, body_text, date_header):
    """Classify one email. Returns (status, data):
         ('txn', parsed)          a completed money movement with amount and card/account
         ('incomplete', partial)  a transaction template matched but the amount or card is missing
         ('noise', reason)        known not-a-transaction (OTP, declined, payment received, ...)
         ('unknown', None)        nothing recognised
    """
    sender = (sender_email or "").strip().lower()
    bank = ALERT_SENDERS.get(sender)
    subj = _normalise(subject)
    body = _normalise(body_text)
    if _NEGATED.search(subj) or _NEGATED.search(body):
        return "noise", "declined_or_failed"

    for name, tbank, rx, kind in _COMPILED:
        if bank and tbank != bank:
            continue
        m = rx.search(body)
        if not m:
            continue
        g = m.groupdict()
        currency, amount = _money(g.get("cur"), g.get("amt"))
        card = g.get("card")
        if name == "axis_cc_spend" and not card:
            # bodies cut to Gmail's snippet lose the card number; the subject still carries it
            sm = re.search(r"credit card no\.? XX(\d{4})", subj, re.I)
            card = sm.group(1) if sm else None
        body_date = parse_body_date(g.get("date")) if g.get("date") else None
        if body_date is None and name == "axis_cc_spend":
            lead = re.match(r"(?:CLICK HERE )?(\d{1,2}-\d{1,2}-\d{4}) ", body)
            body_date = parse_body_date(lead.group(1)) if lead else None
        hdr = header_ist_date(date_header)
        if body_date is not None and hdr:
            # an alert is sent within minutes of the transaction: a body date after the email or
            # months before it means the regex read the wrong number, so trust the header.
            h = _date.fromisoformat(hdr)
            if body_date > h + timedelta(days=2) or body_date < h - timedelta(days=60):
                body_date = None
        txn_date = body_date.isoformat() if body_date else hdr
        if name == "sbi_debit_card":
            ttype = (g.get("ttype") or "").upper()
            if "ENQUIRY" in ttype:
                return "noise", "not_a_transaction"
            if ttype and "WITHDRAWAL" not in ttype:
                kind = "bank_debit"            # a POS / e-com spend on the debit card
        txn_type, context, pattern_kind = _KIND_MAP[kind]
        merchant = _clean_merchant(g.get("merchant") or g.get("loc"))
        if kind == "atm":
            merchant = "ATM CASH WITHDRAWAL" + (f" {merchant}" if merchant else "")
        if not merchant and g.get("vpa"):
            merchant = g["vpa"]
        if not merchant and g.get("dest"):
            digits = re.sub(r"\D", "", g["dest"])
            merchant = f"TRANSFER TO AC X{digits[-4:]}" if digits else "TRANSFER TO ACCOUNT"
            if name == "sbi_yono_si":
                merchant = "SI FUND " + merchant
        if kind == "bank_credit" and merchant and re.search(r"revers", merchant, re.I):
            pattern_kind = "reversal"
        elif kind == "bank_debit" and merchant and re.search(r"\bcharge", merchant, re.I):
            pattern_kind = "fee"
        occurred_at = None
        if g.get("time") and body_date:
            t = g["time"] if g["time"].count(":") == 2 else g["time"] + ":00"
            occurred_at = f"{body_date.isoformat()}T{t.zfill(8)}+05:30"
        parsed = {
            "amount": amount,
            "txn_type": txn_type,
            "card_last4": card,
            "merchant": merchant,
            "date": txn_date,
            "occurred_at": occurred_at,
            "ref_no": g.get("ref") or None,
            "currency": currency,
            "context": context,
            "pattern_kind": pattern_kind,
            "bank": tbank,
            "template": name,
        }
        if amount is None or not card or not txn_date:
            return "incomplete", parsed
        return "txn", parsed

    if _NOISE.search(subj) or _NOISE.search(body):
        return "noise", "not_a_transaction"
    return "unknown", None


def parse_alert_email(sender_email, subject, body_text, date_header):
    """Parse one bank alert email.

    Returns {amount, txn_type, card_last4, merchant, date (IST, YYYY-MM-DD), occurred_at, ref_no,
    currency, context ('email-card' | 'upi' | 'bank' | 'atm'), pattern_kind, bank, template} for a
    completed transaction, or None for OTPs, promos, statements, payment-received notices,
    e-mandate pre-debit notices, declined/failed attempts and anything unreadable.
    `amount` is in `currency` (foreign-currency alerts are converted when stored)."""
    status, data = _recognise(sender_email, subject, body_text, date_header)
    return data if status == "txn" else None


# ---------------------------------------------------------------------------------------------
# Storing
# ---------------------------------------------------------------------------------------------

_FOREIGN_IN_DESC = r"([\d,]+\.\d{2})\s*"      # "CLAUDE.AI SUBSCRIPTION ANTHROPIC.COM CA 118.00 USD"


def fx_rate(conn, currency):
    """INR per unit of `currency`, from the most recent statement line that printed both amounts.

    No fixed table: the bank's own conversion on the last statement is the best estimate we have
    (iter-18 HP4 marks the row fx_estimated, and the statement line replaces it when it lands)."""
    r = conn.execute(
        "SELECT amount, original_amount FROM transactions WHERE currency = ? AND original_amount > 0 "
        "AND COALESCE(fx_estimated, 0) = 0 AND source = 'card' ORDER BY date DESC, id DESC LIMIT 1",
        (currency,),
    ).fetchone()
    if r:
        return float(r["amount"]) / float(r["original_amount"])
    pat = re.compile(_FOREIGN_IN_DESC + re.escape(currency) + r"\b", re.I)
    for r in conn.execute(
        "SELECT amount, raw_description FROM transactions WHERE source = 'card' AND raw_description LIKE ? "
        "ORDER BY date DESC, id DESC LIMIT 20",
        (f"%{currency}%",),
    ):
        m = pat.search(r["raw_description"] or "")
        if m:
            orig = float(m.group(1).replace(",", ""))
            if orig > 0:
                return float(r["amount"]) / orig
    return None


def _email_rows(conn, since, include_quarantined):
    senders = sorted(ALERT_SENDERS)
    marks = ",".join("?" * len(senders))
    q = (
        "SELECT e.id, e.sender_email, e.subject, e.body_snippet, e.body_text, e.date FROM emails e "
        f"WHERE LOWER(e.sender_email) IN ({marks}) "
        "AND NOT EXISTS (SELECT 1 FROM transactions t WHERE t.source = 'email' "
        "                AND t.source_ref = CAST(e.id AS TEXT)) "
    )
    if not include_quarantined:
        q += ("AND NOT EXISTS (SELECT 1 FROM parse_quarantine pq WHERE pq.source = 'email' "
              "AND pq.source_ref = CAST(e.id AS TEXT) AND pq.resolved_at IS NULL) ")
    q += "ORDER BY e.id"
    out = []
    for r in conn.execute(q, senders).fetchall():
        d = header_ist_date(r["date"])
        if since and (d is None or d < since):
            continue
        out.append(r)
    return out


def _body(row):
    return row["body_text"] or row["body_snippet"] or ""


def _family(sender, template):
    return (sender or "").lower(), template


def _twins_of(conn, txn_id, parsed, sender, sent_at, ref):
    """Alert emails already stored that may be the same money movement from a different template.

    Two alerts are the same movement when they name the same card/account, direction, currency
    and amount, come from different (sender, template) families and were sent within TWIN_WINDOW
    of each other. Same-family pairs are genuine repeats (two Rs 287 Blinkit orders) and are left
    alone; two different bank references are two different transactions, and so are two transfers
    whose alerts name different people ('Beneficiary Name Asha Rani' vs 'Transferred to Mr. RAVI
    KUMAR', 70-110 s apart: iteration-198 re-verify). The closest in time comes first."""
    rows = conn.execute(
        "SELECT t.id, t.is_duplicate, t.matched_txn_id, t.ref_no, t.original_amount, t.currency, "
        "t.merchant, t.raw_description, t.category, t.raw_text, t.source, "
        "e.sender_email, e.subject, e.body_snippet, e.body_text, e.date AS email_date "
        "FROM transactions t JOIN emails e ON t.source_ref = CAST(e.id AS TEXT) "
        "WHERE t.source = 'email' AND t.id <> ? AND t.card_last4 = ? AND t.txn_type = ? "
        "AND COALESCE(t.currency, 'INR') = ? AND t.date BETWEEN date(?, '-1 day') AND date(?, '+1 day') "
        "ORDER BY t.id",
        (txn_id, parsed["card_last4"], parsed["txn_type"], parsed["currency"], parsed["date"], parsed["date"]),
    ).fetchall()
    mine = merge._row(conn, txn_id)
    found = []
    for r in rows:
        theirs = parse_alert_email(r["sender_email"], r["subject"], _body(r), r["email_date"])
        if not theirs or abs(float(theirs["amount"]) - float(parsed["amount"])) >= 0.005:
            continue
        if _family(r["sender_email"], theirs["template"]) == _family(sender, parsed["template"]):
            continue
        if ref and r["ref_no"] and r["ref_no"] != ref:
            continue
        their_sent = header_datetime(r["email_date"])
        if sent_at is None or their_sent is None or abs(their_sent - sent_at) > TWIN_WINDOW:
            continue
        if mine is not None and merge.transfer_payees_conflict(mine, r):
            continue
        found.append((abs(their_sent - sent_at), r["id"], r))
    for _gap, _id, r in sorted(found, key=lambda f: (f[0], f[1])):
        yield r


def _sibling_covers(conn, email_row, partial):
    """For an alert that lost its amount (SBI prints 'Trxn. of USD.00' for foreign e-mandate debits):
    is the same debit reported by another alert from the same sender, sent within TWIN_WINDOW,
    whose amount we can read? The e-mandate success mail always accompanies those."""
    sent_at = header_datetime(email_row["date"])
    if sent_at is None:
        return False
    rows = conn.execute(
        "SELECT id, sender_email, subject, body_snippet, body_text, date FROM emails "
        "WHERE LOWER(sender_email) = ? AND id <> ? AND id BETWEEN ? AND ?",
        (email_row["sender_email"].lower(), email_row["id"],
         email_row["id"] - SIBLING_ID_SPAN, email_row["id"] + SIBLING_ID_SPAN),
    ).fetchall()
    for r in rows:
        other = parse_alert_email(r["sender_email"], r["subject"], _body(r), r["date"])
        if not other or other["template"] == partial["template"]:
            continue
        if partial.get("card_last4") and other["card_last4"] != partial["card_last4"]:
            continue
        if partial.get("currency") and other["currency"] != partial["currency"]:
            continue
        their_sent = header_datetime(r["date"])
        if their_sent is not None and abs(their_sent - sent_at) <= TWIN_WINDOW:
            return True
    return False


def _excerpt(subject, body):
    return f"{_normalise(subject)} | {_normalise(body)[:250]}"


def known_accounts(conn):
    """last4 of every card/account the owner has declared (cards table = /data/cards.json, M10).

    Bank-ACCOUNT alerts are only stored for these. The mailbox also receives alerts for SBI
    accounts nobody configured — one of them is a hometown account whose ATM withdrawals and
    pension-like credits (2026 dry run: 60 movements, Rs 12.6 lakh) would otherwise land in the
    owner's spend. Declaring the account in cards.json opts it in. Credit-card alerts are not
    gated: a card in the owner's name is the owner's spend, configured or not (as for SMS)."""
    out = set()
    for r in conn.execute("SELECT last4, pdf_card_last4 FROM cards"):
        for v in (r["last4"], r["pdf_card_last4"]):
            if v:
                out.add(str(v).strip())
    return out


def _process(conn, since, include_quarantined, outcomes=None):
    stats = {"scanned": 0, "inserted": 0, "linked_as_duplicate": 0, "quarantined": 0, "skipped": 0,
             "skip_reasons": {}, "unknown_accounts": {}}

    def skip(reason):
        stats["skipped"] += 1
        stats["skip_reasons"][reason] = stats["skip_reasons"].get(reason, 0) + 1

    accounts = known_accounts(conn)
    stored = []                                  # (email row, parsed, txn id, amount), linked below
    for e in _email_rows(conn, since, include_quarantined):
        stats["scanned"] += 1
        body = _body(e)
        sender = e["sender_email"].lower()
        status, data = _recognise(sender, e["subject"], body, e["date"])

        if status == "noise":
            skip(data)
            continue
        if status == "unknown":
            if _ALERT_SUBJECT.search(_normalise(e["subject"])) or quarantine.looks_like_money_movement(
                    _normalise(body)):
                quarantine.quarantine(conn, "email", e["id"], sender, _excerpt(e["subject"], body), "no_pattern")
                stats["quarantined"] += 1
            else:
                skip("not_a_transaction")
            continue
        if status == "incomplete":
            if len(body) <= SNIPPET_LEN:
                skip("truncated_body")
            elif data.get("amount") is None and _sibling_covers(conn, e, data):
                skip("twin_without_amount")
            else:
                reason = "no_amount" if data.get("amount") is None else "no_card"
                quarantine.quarantine(conn, "email", e["id"], sender, _excerpt(e["subject"], body), reason)
                stats["quarantined"] += 1
            continue

        p = data
        if p["context"] != "email-card" and p["card_last4"] not in accounts:
            skip("unknown_account")
            ua = stats["unknown_accounts"]
            ua[p["card_last4"]] = ua.get(p["card_last4"], 0) + 1
            continue
        amount, original, fx = p["amount"], None, 0
        if p["currency"] != "INR":
            rate = fx_rate(conn, p["currency"])
            if rate is None:
                quarantine.quarantine(conn, "email", e["id"], sender, _excerpt(e["subject"], body),
                                      "fx_rate_unknown")
                stats["quarantined"] += 1
                continue
            original, amount, fx = p["amount"], round(p["amount"] * rate, 2), 1
        raw_desc = p["merchant"] or f"{p['bank']} {'CARD' if p['context'] == 'email-card' else 'ACCOUNT'} TRANSACTION"
        try:
            res = insert_transaction(
                conn, amount=amount, date=p["date"], source="email", source_ref=e["id"],
                raw_description=raw_desc, bank=p["bank"], card_last4=p["card_last4"],
                txn_type=p["txn_type"], raw_text=_excerpt(e["subject"], body), currency=p["currency"],
                original_amount=original, fx_estimated=fx, pattern_kind=p["pattern_kind"],
                ref_no=p["ref_no"], occurred_at=p["occurred_at"], context=p["context"], link=False,
            )
        except ValueError:
            quarantine.quarantine(conn, "email", e["id"], sender, _excerpt(e["subject"], body), "invalid_values")
            stats["quarantined"] += 1
            continue
        if res["status"] != "inserted":
            skip("already_stored")
            continue
        stats["inserted"] += 1
        stored.append((e, p, res["id"], amount))

    # Link only once the whole batch is stored, in the order the bank SENT the alerts (oldest first;
    # Gmail returns a batch newest first). Linked one by one as they were read, T2's core-banking mail
    # took T1's YONO group before T1's own core-banking mail had been stored (iteration-198 re-verify).
    stored.sort(key=lambda s: (header_datetime(s[0]["date"]) or _LAST, s[0]["id"]))
    for e, p, txn_id, amount in stored:          # proof first: a reference, a printed balance / time
        _link_by_proof(conn, p, txn_id)
    conn.commit()
    for e, p, txn_id, amount in stored:          # then the nearest twin or copy
        _link_alert(conn, e, p, txn_id)
        conn.commit()
    for e, p, txn_id, amount in stored:
        row = conn.execute("SELECT is_duplicate, matched_txn_id, merge_rule FROM transactions WHERE id = ?",
                           (txn_id,)).fetchone()
        if row["is_duplicate"]:
            stats["linked_as_duplicate"] += 1
        quarantine.resolve(conn, "email", e["id"], "parsed", transaction_id=txn_id)
        if outcomes is not None:
            outcomes.append({"email_id": e["id"], "txn_id": txn_id, "template": p["template"],
                             "card_last4": p["card_last4"], "date": p["date"], "amount": amount,
                             "txn_type": p["txn_type"], "is_duplicate": row["is_duplicate"],
                             "merge_rule": row["merge_rule"], "matched_txn_id": row["matched_txn_id"]})
    conn.commit()
    return stats


_LAST = datetime.max.replace(tzinfo=timezone.utc)


def _link_by_proof(conn, p, txn_id):
    """First pass over a batch: link an alert only on proof —
      1. its exact bank reference (a twin template of ANOTHER transfer could otherwise take this alert,
         and its reference, before the copy carrying the same reference is considered);
      2. the group that prints the same balance or time (merge.proof_target), even one that already
         holds an alert of another sender family. A guess must not come first: T1's core-banking mail
         would take T2's YONO mail as a twin 70 s away before that YONO mail had joined its own SMS by
         reference (iteration-198 re-verify); and each of two same-amount card mails stored in one batch
         after their statement lines joins the line whose SMS prints its time (round 9)."""
    x = merge._row(conn, txn_id)
    if x is None or x["is_duplicate"]:
        return False
    if p["ref_no"]:
        hit = merge.ref_match(conn, x)
        if hit is not None:
            merge.apply_link(conn, x, hit, "ref")
            return True
    y = merge.proof_target(conn, x)
    if y is not None:
        merge.apply_link(conn, x, y, "proof")
        return True
    return False


def _link_alert(conn, e, p, txn_id):
    """Second pass: an alert no proof placed takes, of
      * a twin alert from another template of the same movement (once the first alert has absorbed the
        SMS/statement copy, merge.py's 1:1 pairing would leave the second alert live: double count),
        every candidate tried, closest first (the first may belong to another transfer whose group
        already holds an alert / reference of its own), and
      * the copy merge.find_match picks,
    the one nearer in time; a twin when only it is timed (e-mandate pairs on a card with no SMS)."""
    x = merge._row(conn, txn_id)
    if x is None or x["is_duplicate"]:
        return                                   # an earlier alert of the batch took it as its twin
    twin_target, twin_gap = _twin_target(conn, x, e, p)
    y, rule = merge.find_match(conn, x)
    if twin_target is not None and (y is None or not merge._gap_s(conn, x, y) < twin_gap):
        merge.apply_link(conn, x, twin_target, "same-alert")
        return
    merge.link_on_insert(conn, txn_id)
    x = merge._row(conn, txn_id)
    if not x["is_duplicate"]:
        # link_on_insert may have released a far copy that kept the twin's group out of reach
        twin_target, _gap = _twin_target(conn, x, e, p)
        if twin_target is not None:
            merge.apply_link(conn, x, twin_target, "same-alert")


def _twin_target(conn, x, e, p):
    """The group x's closest compatible twin alert belongs to, and how far apart they were sent."""
    sent_at = header_datetime(e["date"])
    for twin in _twins_of(conn, x["id"], p, e["sender_email"].lower(), sent_at, p["ref_no"]):
        target_id = twin["matched_txn_id"] if twin["is_duplicate"] and twin["matched_txn_id"] else twin["id"]
        cand = merge._row(conn, target_id)
        if cand is not None and cand["id"] != x["id"] and merge.groups_compatible(conn, x, cand):
            return cand, abs(header_datetime(twin["email_date"]) - sent_at).total_seconds()
    return None, None


def process_bank_alert_emails(conn, since=None):
    """Hourly: turn new bank alert emails into transactions (iter-18 H4). Idempotent.

    `since` is an IST date (YYYY-MM-DD) compared with each email's Date header; by default the last
    HOURLY_LOOKBACK_DAYS. Emails held in the quarantine are left for review here; the backfill
    retries them. Returns {scanned, inserted, linked_as_duplicate, quarantined, skipped, skip_reasons}."""
    if since is None:
        since = (today_ist() - timedelta(days=HOURLY_LOOKBACK_DAYS)).isoformat()
    return _process(conn, since, include_quarantined=False)


def _by_card(outcomes):
    out = {}
    for o in outcomes:
        c = out.setdefault(o["card_last4"] or "-", {"new": 0, "new_amount": 0.0, "linked": 0,
                                                     "linked_amount": 0.0, "by_rule": {}})
        if o["is_duplicate"]:
            c["linked"] += 1
            c["linked_amount"] = round(c["linked_amount"] + o["amount"], 2)
            c["by_rule"][o["merge_rule"]] = c["by_rule"].get(o["merge_rule"], 0) + 1
        else:
            c["new"] += 1
            c["new_amount"] = round(c["new_amount"] + o["amount"], 2)
    return out


def backfill_alert_emails(conn, since="2025-10-01", dry_run=True, outcomes=None):
    """One-off history run (iter-18 H4), retrying anything previously quarantined.

    dry_run=True works on an in-memory copy of the database and discards it, so nothing is
    written; the numbers (what would be new vs linked to an SMS/statement copy, per card) are
    the same the real run produces. Pass a list as `outcomes` to receive one entry per stored
    alert."""
    work = conn
    if dry_run:
        work = sqlite3.connect(":memory:", isolation_level=None)
        conn.backup(work)
        work.row_factory = sqlite3.Row
    collected = [] if outcomes is None else outcomes
    try:
        stats = _process(work, since, include_quarantined=True, outcomes=collected)
    finally:
        if dry_run:
            work.close()
    stats["dry_run"] = dry_run
    stats["since"] = since
    stats["by_card"] = _by_card(collected)
    return stats
