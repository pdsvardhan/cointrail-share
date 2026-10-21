import re

from .base import (Statement, StatementParser, Transaction, extract_card_last4_from_filename,
                   extract_card_last4_from_text, is_payment, iter_lines, parse_amount,
                   parse_date_text)


class HDFCParser(StatementParser):
    """Parse HDFC Bank credit card statements.

    Current layout (2025-10 onwards):
        DD/MM/YYYY| HH:MM  Description  [+ NeuCoins]  [+] C Amount  [l]
    `C` is how the rupee glyph extracts and a `+` IMMEDIATELY before it marks a credit. Tata Neu
    statements have a "Base NeuCoins" column that also prints `+ 42` — that one sits before the
    description's end, not before `C`, and is not a credit marker.

    A long description wraps AROUND the date line, leaving the date line with no text of its own:
        BPPY CC PAYMENT DP000000000000AbCdE (Ref#
        20/06/2026| 21:04 + C 15,336.00 l
        ST000000000000000000001)
    The old parser read that as a Rs 15,336 DEBIT described '+' (iter-18 M2: Rs 4,950 on
    2026-04-06 and Rs 15,336 on 2026-06-20 counted as spend). The description is now rebuilt
    from the surrounding lines, so the payment is recognised and skipped like every other bank's.

    2025 layout: DD/MM/YYYY HH:MM:SS Description Amount[Cr]
    """
    bank = "hdfc"

    TRANSACTION_RE = re.compile(
        r"^(\d{2}/\d{2}/\d{4})\|\s*\d{2}:\d{2}\s*"   # date|time
        r"(.*?)\s*"                                   # description (may be empty: wrapped)
        r"(\+\s*)?C\s*(-?[\d,]+\.\d{2})"              # optional credit marker, C amount
        r"(?:\s+l)?\s*$"                              # PI marker
    )
    TRANSACTION_RE_OLD = re.compile(
        r"^(\d{2}/\d{2}/\d{4})(?:\s+\d{2}:\d{2}(?::\d{2})?)?\s+(.+?)\s+([\d,]+\.\d{2})\s*(Cr)?$"
    )
    # Tata Neu "Base NeuCoins" column ('+ 42') left at the end of the description
    NEUCOINS_RE = re.compile(r"\s+[+-]\s*\d+$")
    HEADER_RE = re.compile(
        r"DATE\s*&\s*TIME|TRANSACTION DESCRIPTION|Domestic Transactions|International Transactions|"
        r"\[CKYC|^Page \d+ of \d+|Credit Card Statement|^Note:|^Date Transaction", re.I)

    def parse_text(self, pages, filename=""):
        lines = list(iter_lines(pages))
        text = "\n".join(l for _, l in lines)
        stmt = Statement(bank=self.bank)
        stmt.card_last4 = extract_card_last4_from_text(text) or extract_card_last4_from_filename(filename)
        self._parse_summary(stmt, lines, text)

        consumed = set()
        for pos, (idx, line) in enumerate(lines):
            if pos in consumed:
                continue
            match = self.TRANSACTION_RE.match(line)
            if match:
                date_str, desc, plus, amt = match.groups()
                desc = self.NEUCOINS_RE.sub("", desc.strip()).strip()
                if not desc:
                    desc = self._wrapped_description(lines, pos, consumed)
                amount = parse_amount(amt)
                is_credit = bool(plus)
            else:
                match = self.TRANSACTION_RE_OLD.match(line)
                if not match:
                    continue
                date_str, desc, amt, cr = match.groups()
                amount = parse_amount(amt)
                is_credit = bool(cr)
            if not amount:
                continue
            txn = Transaction(date=date_str, description=desc.strip(), amount=amount,
                              is_credit=is_credit, card_last4=stmt.card_last4, line_index=idx)
            # Bill payments are not spend (and not refunds): kept aside for reconciliation only.
            (stmt.payments if is_payment(txn.description) else stmt.transactions).append(txn)
        return stmt

    def _wrapped_description(self, lines, pos, consumed):
        """Rebuild the text of a date line whose description wrapped onto the lines around it."""
        parts = []
        if pos > 0 and (pos - 1) not in consumed:
            prev = lines[pos - 1][1]
            if prev and not self._is_txn(prev) and not self.HEADER_RE.search(prev):
                parts.append(prev)
                consumed.add(pos - 1)
        if pos + 1 < len(lines):
            nxt = lines[pos + 1][1]
            opened = parts and parts[0].count("(") > parts[0].count(")")
            if opened and nxt.endswith(")") and not self._is_txn(nxt):
                parts.append(nxt)
                consumed.add(pos + 1)
        return " ".join(parts)

    def _is_txn(self, line):
        return bool(self.TRANSACTION_RE.match(line) or self.TRANSACTION_RE_OLD.match(line))

    def _parse_summary(self, stmt, lines, text):
        """Statement date, billing period, Total Amount Due, Minimum Due and Due Date (HP5).

        HDFC statement SMS stopped carrying the total in 2026-06 (todo #949), so the PDF is the
        only place the bank's own figure arrives for these cards."""
        m = re.search(r"Statement Date\s*:?\s*(\d{2}/\d{2}/\d{4}|\d{2} [A-Za-z]{3},? \d{4})", text)
        if m:
            stmt.statement_date = parse_date_text(m.group(1))
        m = re.search(r"Billing Period\s*(\d{2} [A-Za-z]{3},? \d{4})\s*-\s*(\d{2} [A-Za-z]{3},? \d{4})", text)
        if m:
            stmt.period_start, stmt.period_end = parse_date_text(m.group(1)), parse_date_text(m.group(2))

        for pos, (_, line) in enumerate(lines):
            # "_ C13,836.00" — the TOTAL AMOUNT DUE box ('_' is the leading glyph of the box)
            m = re.match(r"^_\s*C\s*(-?[\d,]+\.\d{2})$", line)
            if m and stmt.total_due is None:
                stmt.total_due = parse_amount(m.group(1))
                continue
            # "C-0.26 C0.00 + C13,836.54 + C0.00 =" — previous dues, payments, purchases, finance
            m = re.match(r"^C\s*(-?[\d,]+\.\d{2})\s+C\s*(-?[\d,]+\.\d{2})\s*\+\s*C\s*(-?[\d,]+\.\d{2})"
                         r"\s*\+\s*C\s*(-?[\d,]+\.\d{2})\s*=?$", line)
            if m and stmt.previous_balance is None:
                prev, pay, purch, fin = (parse_amount(g) for g in m.groups())
                stmt.previous_balance, stmt.payments_credits, stmt.purchases = prev, pay, purch + fin
                continue
            if "MINIMUM DUE" in line and "DUE DATE" in line and pos + 1 < len(lines):
                # next line: "C700.00 21 Oct, 2026"  (or "C0.00 Nil" when nothing is due)
                m = re.match(r"^C\s*(-?[\d,]+\.\d{2})\s+(\d{2} [A-Za-z]{3},? \d{4}|Nil)", lines[pos + 1][1])
                if m:
                    stmt.min_due = parse_amount(m.group(1))
                    stmt.due_date = parse_date_text(m.group(2))
                continue
            if re.search(r"Payment Due Date\s+Total Dues\s+Minimum Amount Due", line):
                # 2025 layout: the values land at the end of a later line: "21/09/2025 9,053.00 460.00"
                for _, nxt in lines[pos + 1:pos + 6]:
                    m = re.search(r"(\d{2}/\d{2}/\d{4})\s+(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})$", nxt)
                    if m:
                        stmt.due_date = parse_date_text(m.group(1))
                        stmt.total_due = parse_amount(m.group(2))
                        stmt.min_due = parse_amount(m.group(3))
                        break
                continue
            if re.match(r"^Opening\s+Payment/", line):
                # 2025 layout account summary: opening, payments, purchases, finance, total dues
                for _, nxt in lines[pos + 1:pos + 5]:
                    m = re.match(r"^(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\s+"
                                 r"(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})$", nxt)
                    if m:
                        prev, pay, purch, fin, _tot = (parse_amount(g) for g in m.groups())
                        stmt.previous_balance, stmt.payments_credits, stmt.purchases = prev, pay, purch + fin
                        break
        if stmt.total_due is None and stmt.previous_balance is not None:
            stmt.total_due = round(stmt.previous_balance - stmt.payments_credits + stmt.purchases, 2)
