import re

from .base import (MONTHS, Statement, StatementParser, Transaction, extract_card_last4_from_filename,
                   extract_card_last4_from_text, is_payment, iter_lines, parse_amount,
                   parse_date_text, resolve_year)


class HSBCParser(StatementParser):
    """Parse HSBC India credit card statements.

    Real format (from e-statements):
        22 JAN 2026 4,295.99                              <- payment due date, minimum due
        XXXXXXXXXXX 05 DEC 2025 To 04 JAN 2026 4,295.99   <- statement period, total amount due
        07FEB  BBPS PMT BBPSDP0000000000000AbCd  3,867.00 CR
        17FEB  IAP Myntra Designs Pvt Ltd BANGALORE  11,757.00

    Pattern: DDMON  DESCRIPTION  AMOUNT [CR]. Lines carry no year: it comes from the statement
    period printed on the same page (iter-18 H8 — the old fallback was a hard-coded "2026", so
    every line of the first 2027 statement would have been dated 2026). A Dec->Jan period puts
    December lines in the earlier year; with no period printed, today (IST) is the reference.
    """
    bank = "hsbc"

    TRANSACTION_RE = re.compile(
        r"^(\d{2})([A-Z]{3})\s+(.+?)\s+([\d,]+\.\d{2})(?:\s+(CR))?$"
    )
    # Statement period line: "05 FEB 2026 To 04 MAR 2026" [total amount due]
    PERIOD_RE = re.compile(
        r"(\d{2}\s+[A-Z]{3}\s+\d{4})\s+To\s+(\d{2}\s+[A-Z]{3}\s+\d{4})(?:\s+(-?[\d,]+\.\d{2}))?"
    )
    DUE_RE = re.compile(r"^(\d{2}\s+[A-Z]{3}\s+\d{4})\s+(-?[\d,]+\.\d{2})$")

    SKIP_DESCRIPTIONS = {
        "opening balance",
        "net outstanding balance",
        "total purchase outstanding",
        "total cash outstanding",
        "total balance transfer outstanding",
        "total loan outstanding",
        "available credit limit",
    }

    def parse_text(self, pages, filename=""):
        lines = list(iter_lines(pages))
        text = "\n".join(l for _, l in lines)
        stmt = Statement(bank=self.bank)
        stmt.card_last4 = extract_card_last4_from_text(text) or extract_card_last4_from_filename(filename)

        period = self.PERIOD_RE.search(text)
        if period:
            stmt.period_start, stmt.period_end = parse_date_text(period.group(1)), parse_date_text(period.group(2))
            stmt.statement_date = stmt.period_end
            if period.group(3):
                stmt.total_due = parse_amount(period.group(3))
        for _, line in lines[:5]:
            due = self.DUE_RE.match(line)
            if due:
                stmt.due_date, stmt.min_due = parse_date_text(due.group(1)), parse_amount(due.group(2))
                break
        # Account summary under the NET OUTSTANDING BALANCE line: opening, debits, credits, closing
        m = re.search(r"NET OUTSTANDING BALANCE\s+-?[\d,]+\.\d{2}\s*\n\s*(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})"
                      r"\s+(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})", text)
        if m:
            stmt.previous_balance, stmt.purchases, stmt.payments_credits = (
                parse_amount(m.group(1)), parse_amount(m.group(2)), parse_amount(m.group(3)))

        for idx, line in lines:
            match = self.TRANSACTION_RE.match(line)
            if not match:
                continue
            day, mon_str, desc, amt_str, cr = match.groups()
            desc = desc.strip()
            if desc.lower() in self.SKIP_DESCRIPTIONS:
                continue
            mon = MONTHS.get(mon_str)
            amount = parse_amount(amt_str)
            if not mon or not amount:
                continue
            year = resolve_year(int(mon), int(day), stmt.reference_date)
            txn = Transaction(date=f"{year}-{mon}-{day}", description=desc, amount=amount,
                              is_credit=bool(cr), card_last4=stmt.card_last4, line_index=idx)
            (stmt.payments if is_payment(desc) else stmt.transactions).append(txn)
        return stmt
