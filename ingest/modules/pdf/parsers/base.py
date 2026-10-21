"""Shared pieces for the credit-card statement parsers.

iter-18 changes (2026-10-20 audit):
  H8   No year is ever hard-coded. A date printed without a year ('21DEC') takes its year from
       the statement itself (statement date / period end) through util.timez.infer_year, so a
       Dec->Jan statement puts December lines in the earlier year; today (IST) is only the
       fallback when a statement prints no date at all.
  HP5  A parser returns a Statement: the transaction lines PLUS what the bank printed about the
       bill (Total Amount Due, Minimum Amount Due, Payment Due Date, statement date / period and
       the account-summary figures used to reconcile the parse).
  Line identity: every Transaction carries `line_index`, its position in the statement's text,
       so two identical lines in one statement stay two transactions (the old per-file `seen`
       sets collapsed them) and a re-parse of the same file maps each line to the same row.
  Card identity: the owner's card/account numbers are no longer hard-coded here. Parsers report
       what the statement prints (card last4, or an account suffix such as SBI's); the store
       resolves it against the cards table (cards.pdf_card_last4) — see modules/pdf/store.py.
"""
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from util.timez import infer_year, today_ist

MONTHS = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}

_AMOUNT = re.compile(r"-?[\d,]+(?:\.\d+)?")

# Card-bill payments are not spend and not refunds: every parser drops them from the transaction
# list (they are captured as payments elsewhere) and keeps them in Statement.payments for
# reconciliation. Merchant names that merely contain "payment" (e.g. "Razorpay Payments") must
# NOT match, hence the specific phrases. HDFC prints BillDesk bill payments as "BPPY CC PAYMENT"
# or "BPPY PYMT"; HSBC as "BBPS PMT" / "CREDIT CARD PAYMENT"; HDFC's 2025 layout as
# "TELE TRANSFER CREDIT"; SBI auto-debits as "DIRECT DEBIT PAYMENT(ACH)". A bounced payment comes
# back as a debit ("INSUFFICIENT FUNDS", SBI 2026-06-08) — the reversal of a payment is no more a
# purchase than the payment was a refund, so it is set aside the same way (the dishonour FEE is a
# real charge and stays).
_PAYMENT = re.compile(
    r"payment\s*received|\bcc\s*payment|\bbppy\b|\bbbps\s*(?:pmt|payment)|credit\s*card\s*payment|"
    r"tele\s*transfer\s*credit|direct\s*debit\s*payment|thank\s*you\s*for\s*(?:your\s*|the\s*)?payment|"
    r"\bautopay\s*payment|^insufficient\s*funds$|payment\s*reversal",
    re.I)


def is_payment(description) -> bool:
    """Is this statement line a payment towards the card bill?"""
    return bool(_PAYMENT.search(description or ""))


def parse_amount(text) -> Optional[float]:
    """'1,08,766.63' / '`16,594.97' / 'C-0.14' / '₹ 500' -> float, or None."""
    if text is None:
        return None
    m = _AMOUNT.search(str(text).replace("`", "").replace("₹", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def month_number(name) -> Optional[int]:
    mon = MONTHS.get((name or "")[:3].upper())
    return int(mon) if mon else None


def parse_date_text(text) -> Optional[date]:
    """A date printed WITH its year, in any of the statement layouts:
    '20/08/2026', '2026-08-20', 'August 20, 2026', '20 Aug 2026', '01 Jul, 2026', '19 Sep 26',
    '05 DEC 2025'. Returns None for anything else (including year-less dates)."""
    if not text:
        return None
    s = text.strip()
    try:
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", s)
        if m:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        m = re.match(r"^([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})$", s)
        if m and month_number(m.group(1)):
            return date(int(m.group(3)), month_number(m.group(1)), int(m.group(2)))
        m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,9}),?\s+(\d{4}|\d{2})$", s)
        if m and month_number(m.group(2)):
            y = int(m.group(3))
            return date(y + 2000 if y < 100 else y, month_number(m.group(2)), int(m.group(1)))
    except ValueError:
        return None
    return None


def resolve_year(month, day, reference=None) -> int:
    """Year for a date the statement printed without one (H8).

    `reference` is the statement's own date (or period end). A line can only predate its
    statement, so a December line on a statement dated 4 January is the previous year;
    infer_year implements exactly that. Without a statement date, today (IST) is the reference."""
    return infer_year(int(month), int(day), reference or today_ist())


def normalize_date(d, reference=None):
    """Normalize a statement date to YYYY-MM-DD; year-less 'DDMON' takes its year from
    `reference` (the statement date), never from a constant."""
    if not d:
        return d
    s = d.strip()
    parsed = parse_date_text(s)
    if parsed:
        return parsed.isoformat()
    m = re.match(r"^(\d{1,2})\s*([A-Za-z]{3})$", s)
    if m and month_number(m.group(2)):
        mon, day = month_number(m.group(2)), int(m.group(1))
        return f"{resolve_year(mon, day, reference)}-{mon:02d}-{day:02d}"
    return s


def extract_card_last4_from_text(text):
    """Card last 4 digits as printed in the statement (may be a short suffix for SBI)."""
    # HDFC: Credit Card No. 434155XXXXXX1234
    m = re.search(r"Credit Card No\.\s*\d{4,6}[Xx*]+(\d{4})", text)
    if m:
        return m.group(1)
    # HDFC 2025 layout: Card No: 6529 26XX XXXX 1234
    m = re.search(r"Card No:?\s*\d{4}\s+\d{2}[Xx]{2}\s+[Xx]{4}\s+(\d{4})", text)
    if m:
        return m.group(1)
    # Axis: 539494******1234
    m = re.search(r"\d{4,6}\*{2,}\d*\**(\d{4})", text)
    if m:
        return m.group(1)
    # ICICI: 4315XXXXXXXX1234
    m = re.search(r"\b\d{4}X{6,10}(\d{4})\b", text)
    if m:
        return m.group(1)
    # HSBC: 48xx xxxx xxxx 1234
    m = re.search(r"\d{2}xx\s+xxxx\s+xxxx\s+(\d{4})", text, re.IGNORECASE)
    if m:
        return m.group(1)
    # SBI: XXXX XXXX XXXX XX11 (only the last two digits are printed)
    m = re.search(r"[Xx*]{4}\s+[Xx*]{4}\s+[Xx*]{4}\s+[Xx*]{0,2}(\d{2,4})", text)
    if m:
        return m.group(1)
    return ""


def extract_card_last4_from_filename(filename):
    """Card identifier from the statement filename, as the bank wrote it.

    - ICICI: 4315XXXXXXXX1234_... -> '1234' (the real card last4)
    - SBI:   <16-digit ACCOUNT number>_19092026.pdf -> its last 4 digits, an alias that
             cards.pdf_card_last4 maps to the card (resolved by the store, not hard-coded here)
    - HDFC:  4321XXXXXXXXXX12_... carries only two PAN digits, which are not the card's last4;
             HDFC statements always print the card number, so nothing is guessed from the name.
    """
    basename = os.path.basename(filename)
    m = re.match(r"\d{4}[Xx]{6,10}(\d{4})[_.]", basename)
    if m:
        return m.group(1)
    m = re.match(r"(\d{16})[_.]", basename)
    if m:
        return m.group(1)[-4:]
    return ""


def iter_lines(pages):
    """(line_index, stripped line) over every page; the index is global to the statement and is
    what a transaction's source_ref is built from."""
    idx = 0
    for page in pages:
        for line in (page or "").split("\n"):
            yield idx, line.strip()
            idx += 1


def extract_pages(pdf_path) -> List[str]:
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        return [(page.extract_text() or "") for page in pdf.pages]


@dataclass
class Transaction:
    date: str
    description: str
    amount: float
    is_credit: bool = False
    category: str = "Uncategorized"
    card_last4: str = ""
    line_index: int = -1             # position of the line in the statement text (line identity)
    pattern_kind: Optional[str] = None   # what the layout itself says (e.g. SBI 'M' = EMI instalment)
    currency: str = "INR"            # original currency of a foreign spend; `amount` is always INR
    original_amount: Optional[float] = None

    def __post_init__(self):
        self.date = normalize_date(self.date)


@dataclass
class Statement:
    """One parsed statement: its lines and what the bank printed about the bill (HP5)."""
    bank: str
    card_last4: str = ""             # as printed / from the filename; resolved by the store
    card_suffix: str = ""            # a shorter suffix printed on the face (SBI 'XX11'), if any
    statement_date: Optional[date] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    total_due: Optional[float] = None    # printed Total Amount Due; negative = credit balance
    min_due: Optional[float] = None
    due_date: Optional[date] = None
    previous_balance: Optional[float] = None   # account-summary figures, for reconciliation
    purchases: Optional[float] = None
    payments_credits: Optional[float] = None
    transactions: List[Transaction] = field(default_factory=list)
    payments: List[Transaction] = field(default_factory=list)   # bill payments (not stored)

    @property
    def reference_date(self) -> Optional[date]:
        """The statement's own date — the anchor for any year-less date on it (H8)."""
        return self.statement_date or self.period_end

    def summary(self) -> dict:
        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        return {
            "card_last4": self.card_last4, "statement_date": iso(self.statement_date),
            "period": [iso(self.period_start), iso(self.period_end)],
            "total_due": self.total_due, "min_due": self.min_due, "due_date": iso(self.due_date),
            "previous_balance": self.previous_balance, "purchases": self.purchases,
            "payments_credits": self.payments_credits,
            "lines": len(self.transactions), "payments": len(self.payments),
        }


class StatementParser(ABC):
    """A bank's statement layout. Subclasses implement parse_text over the extracted page text,
    so the layout logic is testable from text fixtures without a PDF."""
    bank = ""

    @abstractmethod
    def parse_text(self, pages: List[str], filename: str = "") -> Statement:
        """Parse the pages' text into a Statement."""

    def parse_statement(self, pdf_path: str) -> Statement:
        return self.parse_text(extract_pages(pdf_path), filename=os.path.basename(pdf_path))

    def parse(self, pdf_path: str) -> List[Transaction]:
        return self.parse_statement(pdf_path).transactions
