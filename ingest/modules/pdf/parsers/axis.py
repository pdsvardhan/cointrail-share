import re

from .base import (Statement, StatementParser, Transaction, extract_card_last4_from_filename,
                   extract_card_last4_from_text, is_payment, iter_lines, parse_amount,
                   parse_date_text)


class AxisParser(StatementParser):
    """Parse Axis Bank credit card statements.

    Format: DD/MM/YYYY  Description,Location  CATEGORY  Amount Dr/Cr
    Summary: "Total Payment Due Minimum Payment Due Statement Period Payment Due Date Statement
    Generation Date" followed by "1,051.38 Dr 100.00 Dr 14/09/2026 - 12/10/2026 01/11/2026 12/10/2026".
    """
    bank = "axis"

    TRANSACTION_RE = re.compile(
        r"(\d{2}/\d{2}/\d{4})\s+"     # date
        r"(.+?)\s+"                     # description
        r"([\d,]+\.\d{2})\s+"          # amount
        r"(Dr|Cr)"                      # debit/credit
    )
    SUMMARY_RE = re.compile(
        r"^(-?[\d,]+\.\d{2})\s*(Dr|Cr)?\s+(-?[\d,]+\.\d{2})\s*(Dr|Cr)?\s+"
        r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})"
    )
    ACCOUNT_RE = re.compile(
        r"^(-?[\d,]+\.\d{2})\s*(Dr|Cr)?\s+(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\s+"
        r"(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\s*(Dr|Cr)?"
    )

    def parse_text(self, pages, filename=""):
        lines = list(iter_lines(pages))
        text = "\n".join(l for _, l in lines)
        stmt = Statement(bank=self.bank)
        stmt.card_last4 = extract_card_last4_from_text(text) or extract_card_last4_from_filename(filename)

        for idx, line in lines:
            summary = self.SUMMARY_RE.match(line)
            if summary and stmt.total_due is None:
                tad, tad_side, mad, _, start, end, due, generated = summary.groups()
                # "Cr" on the total = the bank owes the cardholder (credit balance)
                stmt.total_due = parse_amount(tad) * (-1 if tad_side == "Cr" else 1)
                stmt.min_due = parse_amount(mad)
                stmt.period_start, stmt.period_end = parse_date_text(start), parse_date_text(end)
                stmt.due_date, stmt.statement_date = parse_date_text(due), parse_date_text(generated)
                continue
            account = self.ACCOUNT_RE.match(line)
            if account and stmt.previous_balance is None and stmt.total_due is not None:
                # Previous Balance - Payments - Credits + Purchase + Cash Advance + Other Debit&Charges
                prev, prev_side, pay, cred, purch, cash, other, _tot, _side = account.groups()
                stmt.previous_balance = parse_amount(prev) * (-1 if prev_side == "Cr" else 1)
                stmt.payments_credits = parse_amount(pay) + parse_amount(cred)
                stmt.purchases = parse_amount(purch) + parse_amount(cash) + parse_amount(other)
                continue
            match = self.TRANSACTION_RE.search(line)
            if not match:
                continue
            amount = parse_amount(match.group(3))
            if not amount:
                continue
            txn = Transaction(date=match.group(1), description=match.group(2).strip(), amount=amount,
                              is_credit=match.group(4) == "Cr", card_last4=stmt.card_last4,
                              line_index=idx)
            (stmt.payments if is_payment(txn.description) else stmt.transactions).append(txn)
        return stmt
