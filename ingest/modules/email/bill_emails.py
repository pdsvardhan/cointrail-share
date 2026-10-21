"""
Bill and bill-payment facts from bank and CRED emails — iter-18 HP5 / M1.

Before iter-18 the due date shown for a card was `statement close + configured due_day` and a bill
counted as paid only if the bank sent a "payment received" SMS. Both facts already arrive by email:
the issuers' statement emails print the total, minimum and due date; CRED relays the same bill
("New bill for your … card", "bill is due on …") and confirms every payment made through it
("your credit card bill payment was successful"). Bills paid through CRED had no bank SMS at all,
so seven of them (Rs 1,00,663) were invisible (M1).

This module only *reads* an email (sender, subject, stored body text) and returns plain facts.
Which statement a fact belongs to, and whether a bank figure beats a CRED one, is decided in
services/cards_service.py. Bodies are stored HTML-stripped in `emails.body_snippet`, still carrying
entities (`&#8377;` for ₹) and long runs of layout whitespace, so every parser first normalises.
"""
import html
import re
from datetime import date, timedelta, timezone
from email.utils import parsedate_to_datetime

from util.timez import IST

CRED_SENDERS = {"protect@cred.club"}
ICICI_CARD_SENDERS = {"credit_cards@icici.bank.in", "credit_cards@icicibank.com"}
SBI_STATEMENT_SENDERS = {"statements@sbicard.com", "aurumcardstatement@sbicard.com"}
AXIS_STATEMENT_SENDERS = {"cc.statements@axis.bank.in", "cc.statements@axisbank.com"}
BILL_SENDERS = CRED_SENDERS | ICICI_CARD_SENDERS | SBI_STATEMENT_SENDERS | AXIS_STATEMENT_SENDERS
PAYMENT_SENDERS = CRED_SENDERS | ICICI_CARD_SENDERS

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def clean_text(text):
    """Unescape HTML entities and collapse layout whitespace (incl. NBSP / zero-width chars)."""
    s = html.unescape(text or "")
    s = re.sub(r"[​-‏⁠﻿͏]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def email_ist_date(date_header):
    """IST calendar date of an email's Date header (RFC 2822, any offset), or None."""
    if not date_header:
        return None
    try:
        dt = parsedate_to_datetime(str(date_header).strip())
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:          # a header with no zone is UTC by RFC 2822 convention
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).date()


def _amount(s):
    return float(str(s).replace(",", "").rstrip("."))


def _month(name):
    return _MONTHS.get(str(name)[:3].lower())


def _iso(day, month_name_or_num, year):
    mm = int(month_name_or_num) if str(month_name_or_num).isdigit() else _month(month_name_or_num)
    if not mm:
        return None
    y = int(year)
    y = y + 2000 if y < 100 else y
    try:
        return date(y, mm, int(day)).isoformat()
    except ValueError:
        return None


def _upcoming_iso(day, month_name, reference):
    """A due date printed without its year (older CRED snippets were cut at 'November 07,'):
    the occurrence nearest after `reference` minus a grace, since bills are announced ahead of
    their due date but reminders keep coming a few days after it."""
    mm = _month(month_name)
    if not mm or reference is None:
        return None
    for y in (reference.year - 1, reference.year, reference.year + 1):
        try:
            d = date(y, mm, int(day))
        except ValueError:
            continue
        if reference - timedelta(days=30) <= d <= reference + timedelta(days=330):
            return d.isoformat()
    return None


# --- CRED: bill generated / bill due -------------------------------------------------------------
# Every CRED template names the card as "<Bank> XXXX-XXXX-XXXX-nnnn", "<Bank> •••• nnnn" or
# "<Bank> .... xxxx nnnn". Amounts carry ₹ (stored as &#8377;).
_CARD = r"(?:X{4}-X{4}-X{4}-|•+\s*|\.{3,}\s*x{4}\s*)(\d{4})"
_MON_D_Y = r"([A-Za-z]+)\s+(\d{1,2}),?\s*(\d{4})?"

# "Here is your April Bill Axis Bank XXXX-XXXX-XXXX-9320 total amount due minimum amount due
#  due date ₹592.00 ₹100.00 May 02, 2026"
_CRED_BILL_OLD = re.compile(
    _CARD + r"\s*total amount due\s*minimum amount due\s*due date\s*₹\s*(-?[\d,]+\.?\d*)\s*₹\s*([\d,]+\.?\d*)\s*"
    + _MON_D_Y, re.I)
# "your credit card bill for October has been generated Axis Bank •••• 9320 bill summary total amount
#  due ₹1,051.38 minimum due ₹100.00 due date November 01, 2026 bill generated on Oct 11, 2026"
_CRED_BILL_NEW = re.compile(
    _CARD + r"\s*bill summary\s*total amount due\s*₹\s*(-?[\d,]+\.?\d*)\s*minimum due\s*₹\s*([\d,]+\.?\d*)\s*"
    r"due date\s*" + _MON_D_Y + r"(?:\s*bill generated on\s*" + _MON_D_Y + r")?", re.I)
# "hi, your credit card payment is due payment due by Oct 03, 2025 Axis Bank .... xxxx 9320 total due ₹419.23"
# "hi, finish paying your credit card bill payment due by Mar 10, 2026 ICICI Bank .... xxxx 0026 remaining due ₹101372.79"
_CRED_DUE_OLD = re.compile(
    r"payment due by\s*" + _MON_D_Y + r"\s*[A-Za-z ]*?" + _CARD + r"\s*(total|remaining) due\s*₹\s*([\d,]+\.?\d*)", re.I)
# "your credit card payment is due HDFC Bank •••• 4956 Due amount Due date ₹13,836 Oct 21, 2026"
# "finish paying your credit card bill ICICI Bank •••• 0026 Remaining due Due date ₹58,766.63 Sep 07, 2026"
_CRED_DUE_NEW = re.compile(
    _CARD + r"\s*(Due amount|Remaining due)\s*Due date\s*₹\s*([\d,]+\.?\d*)\s*" + _MON_D_Y, re.I)

# --- Bank statement emails ------------------------------------------------------------------------
# ICICI: "Payment due by October 8, 2026 ICICI Bank Credit Card XX0026 Minimum Amount Due: ₹5,440.00
#  Total Amount Due: ₹16,469.81" (colon optional; "₹1,000.48 CR" = credit balance)
_ICICI_STMT = re.compile(
    r"Payment due by\s*([A-Za-z]+)\s+(\d{1,2}),?\s*(\d{4})\s*ICICI Bank Credit Card\s*XX(\d{4})\s*"
    r"Minimum Amount Due:?\s*(?:₹|Rs\.?|INR)?\s*([\d,]+\.?\d*)\s*Total Amount Due:?\s*(?:₹|Rs\.?|INR)?\s*([\d,]+\.?\d*)(\s*CR\b)?",
    re.I)
# SBI (Cashback and AURUM): "... SBI Credit Card ending with XXXX XXXX XXXX XX11 ... Total amount due ( )
#  46,088.00 Minimum amount due ( ) 5,598.00 Payment due date 09-Oct-2026" — or "... 0.00 Payment due
#  date NO PAYMENT REQUIRED". Only the last two digits are printed.
_SBI_CARD = re.compile(r"Card ending with\s*(?:X{4}\s*){3}XX(\d{2})", re.I)
_SBI_STMT = re.compile(
    r"Total amount due\s*(?:\([^)]{0,6}\))?\s*(-?[\d,]+\.\d{2})(\s*CR\b)?\s*Minimum amount due\s*(?:\([^)]{0,6}\))?\s*"
    r"([\d,]+\.\d{2})\s*Payment due date\s*(?:(\d{1,2})-([A-Za-z]{3})-(\d{4})|(NO PAYMENT REQUIRED))", re.I)
# Axis: subject "... Credit Card Statement ending XX20 - October 2026"; body "Total Amount Due INR Minimum
#  Amount Due (INR) Payment Due Date (DD-MM-YYYY) 1051.38 Dr 100 Dr 01/11/2026"
_AXIS_SUBJ_CARD = re.compile(r"Statement ending\s*XX(\d{2,4})", re.I)
_AXIS_STMT = re.compile(
    r"Total Amount Due INR Minimum Amount Due \(INR\) Payment Due Date \(DD-MM-YYYY\)\s*"
    r"([\d,]+\.?\d*)\s*(Dr|Cr)?\s*([\d,]+\.?\d*)\s*(?:Dr|Cr)?\s*(\d{2})/(\d{2})/(\d{4})", re.I)

# --- Payments ---------------------------------------------------------------------------------------
# CRED: "Your credit card payment was successful [in 44 seconds] ICICI Bank •••• 0026 payment details
#  amount paid ₹16,469.81 payment date Oct 07, 2026 credited to card Oct 07, 2026 transaction summary
#  Order ID: Gqzzzzzzz005"
_CRED_PAID = re.compile(
    r"credit card payment was successful.*?" + _CARD + r"\s*payment details\s*amount paid\s*₹\s*([\d,]+\.?\d*)\s*"
    r"payment date\s*" + _MON_D_Y, re.I)
_CRED_ORDER = re.compile(r"Order ID:\s*([A-Z0-9]+)", re.I)
# ICICI: "We have received payment of INR 16,469.81 on your ICICI Bank Credit Card account 4315 XXXX XXXX
#  0026 on 07-Oct-2026."
_ICICI_PAID = re.compile(
    r"received payment of (?:INR|Rs\.?|₹)\s*([\d,]+\.?\d*) on your ICICI Bank Credit Card account\s*"
    r"\d{4}\s*X{4}\s*X{4}\s*(\d{4}) on (\d{1,2})-([A-Za-z]{3})-(\d{4})", re.I)


def _due_iso(mon, day, year, ref):
    return _iso(day, mon, year) if year else _upcoming_iso(day, mon, ref)


def parse_bill_email(sender_email, subject, body, email_date=None):
    """Statement facts from one email, or None.

    Returns {'card_last4' | 'card_suffix', 'bank', 'total_due' (None = the email states only a
    REMAINING amount, so it must not become the statement total), 'min_due', 'due_date',
    'statement_date', 'authority' ('bank'|'cred'), 'template'}. `email_date` is the IST date
    the email was sent; it dates year-less due dates and statements that print no due date."""
    sender = (sender_email or "").lower().strip()
    text = clean_text(body)
    subj = clean_text(subject)
    if sender in CRED_SENDERS:
        return _parse_cred_bill(text, email_date)
    if sender in ICICI_CARD_SENDERS:
        m = _ICICI_STMT.search(text)
        if not m:
            return None
        total = _amount(m.group(6))
        return {"card_last4": m.group(4), "bank": "ICICI", "total_due": -total if m.group(7) else total,
                "min_due": _amount(m.group(5)), "due_date": _iso(m.group(2), m.group(1), m.group(3)),
                "statement_date": email_date.isoformat() if email_date else None,
                "authority": "bank", "template": "icici-statement-email"}
    if sender in SBI_STATEMENT_SENDERS:
        c, m = _SBI_CARD.search(text), _SBI_STMT.search(text)
        if not (c and m):
            return None
        total = _amount(m.group(1))
        due = _iso(m.group(4), m.group(5), m.group(6)) if m.group(4) else None
        if not due and not email_date:
            return None
        return {"card_suffix": c.group(1), "bank": "SBI", "total_due": -abs(total) if m.group(2) else total,
                "min_due": _amount(m.group(3)), "due_date": due,
                "statement_date": email_date.isoformat() if email_date else None,
                "authority": "bank", "template": "sbi-statement-email"}
    if sender in AXIS_STATEMENT_SENDERS:
        c, m = _AXIS_SUBJ_CARD.search(subj), _AXIS_STMT.search(text)
        if not (c and m):
            return None
        total = _amount(m.group(1))
        out = {"bank": "Axis", "total_due": -total if (m.group(2) or "").lower() == "cr" else total,
               "min_due": _amount(m.group(3)), "due_date": _iso(m.group(4), m.group(5), m.group(6)),
               "statement_date": email_date.isoformat() if email_date else None,
               "authority": "bank", "template": "axis-statement-email"}
        out["card_last4" if len(c.group(1)) == 4 else "card_suffix"] = c.group(1)
        return out
    return None


def _parse_cred_bill(text, ref):
    m = _CRED_BILL_NEW.search(text)
    if m:
        gen = _iso(m.group(8), m.group(7), m.group(9)) if m.group(7) and m.group(9) else None
        return {"card_last4": m.group(1), "bank": None, "total_due": _amount(m.group(2)),
                "min_due": _amount(m.group(3)), "due_date": _due_iso(m.group(4), m.group(5), m.group(6), ref),
                "statement_date": gen, "authority": "cred", "template": "cred-new-bill"}
    m = _CRED_BILL_OLD.search(text)
    if m:
        return {"card_last4": m.group(1), "bank": None, "total_due": _amount(m.group(2)),
                "min_due": _amount(m.group(3)), "due_date": _due_iso(m.group(4), m.group(5), m.group(6), ref),
                "statement_date": None, "authority": "cred", "template": "cred-bill"}
    m = _CRED_DUE_NEW.search(text)
    if m:
        remaining = m.group(2).lower().startswith("remaining")
        return {"card_last4": m.group(1), "bank": None,
                # "Remaining due" is what is LEFT after a part-payment, not the statement total (HP5).
                "total_due": None if remaining else _amount(m.group(3)), "min_due": None,
                "due_date": _due_iso(m.group(4), m.group(5), m.group(6), ref), "statement_date": None,
                "authority": "cred", "template": "cred-remaining" if remaining else "cred-due"}
    m = _CRED_DUE_OLD.search(text)
    if m:
        remaining = m.group(5).lower() == "remaining"
        return {"card_last4": m.group(4), "bank": None,
                "total_due": None if remaining else _amount(m.group(6)), "min_due": None,
                "due_date": _due_iso(m.group(1), m.group(2), m.group(3), ref), "statement_date": None,
                "authority": "cred", "template": "cred-remaining-old" if remaining else "cred-due-old"}
    return None


def parse_payment_email(sender_email, subject, body, email_date=None):
    """A card-bill payment confirmed by email, or None.

    Returns {'card_last4', 'amount', 'payment_date', 'authority' ('cred'|'bank'), 'ref', 'template'}."""
    sender = (sender_email or "").lower().strip()
    text = clean_text(body)
    if sender in CRED_SENDERS:
        m = _CRED_PAID.search(text)
        if not m:
            return None
        paid_on = _iso(m.group(4), m.group(3), m.group(5)) if m.group(5) else _upcoming_iso(m.group(4), m.group(3), email_date)
        if not paid_on:
            return None
        order = _CRED_ORDER.search(text)
        return {"card_last4": m.group(1), "amount": _amount(m.group(2)), "payment_date": paid_on,
                "authority": "cred", "ref": f"cred:{order.group(1)}" if order else None,
                "template": "cred-payment-success"}
    if sender in ICICI_CARD_SENDERS:
        m = _ICICI_PAID.search(text)
        if not m:
            return None
        paid_on = _iso(m.group(3), m.group(4), m.group(5))
        if not paid_on:
            return None
        return {"card_last4": m.group(2), "amount": _amount(m.group(1)), "payment_date": paid_on,
                "authority": "bank", "ref": None, "template": "icici-payment-received"}
    return None
