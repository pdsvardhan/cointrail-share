import re

from .base import (Statement, StatementParser, Transaction, extract_card_last4_from_filename,
                   extract_card_last4_from_text, is_payment, iter_lines, parse_amount,
                   parse_date_text)


class SBIParser(StatementParser):
    """Parse SBI Card credit card statements.

    Real format (type codes: D=Debit, C=Credit, M=Monthly Instalment, T=Temporary Credit):
        25 Dec 25 BOOKMYSHOW MUMBAI IN 829.86 D
        19 Sep 26 FP EMI 02/06(EXCL TAX 56.03) 4,715.88 M
        19 Sep 26 INTEREST ON EMI 311.26 D
        FORGN CURR MARKUP DB (EXCL TAX 71.18) 395.47 D        <- charge printed without a date
        IGST DB @ 18.00% 127.21 D
    A long description wraps around its date line:
        ANTHROPIC* CLAUDE SUB ANTHROPIC.COM CA 118.00 USD (Pay in
        24 Aug 26 11,299.11 D
        EMIs)
    The old parser required a description on the date line and a D/C code, so it dropped the
    monthly wrapped subscription (~Rs 11k), every 'M' EMI instalment and the undated
    markup/GST charges — the unexplained SBI Cashback statement gaps of the 2026-10-19 audit (P13).
    Dates carry a two-digit year, so H8 does not apply to the lines themselves.
    """
    bank = "sbi"

    DATED_RE = re.compile(
        r"^(\d{2}\s+[A-Za-z]{3}\s+\d{2})\s+"   # DD Mon YY
        r"(.*?)\s*"                               # description (may be empty: wrapped)
        r"([\d,]+\.\d{2})\s+"                     # amount (INR)
        r"([DCMT])$"                              # type code
    )
    UNDATED_RE = re.compile(r"^([A-Za-z].*?)\s+([\d,]+\.\d{2})\s+([DCMT])$")
    # "118.00 USD" inside a description: the original amount of a foreign spend
    FOREIGN_RE = re.compile(r"\s+([\d,]+\.\d{2})\s+(USD|EUR|GBP|SGD|AED|AUD|CAD|CHF|JPY|HKD|THB|MYR|"
                            r"NZD|SAR|QAR|LKR)\b")
    TABLE_START_RE = re.compile(r"^Date\s+Transaction Details|^Transaction Details$|^TRANSACTIONS FOR\b")
    TABLE_END_RE = re.compile(r"^Transactions highlighted|^C=Credit|^SAVINGS AND BENEFITS|^Important Messages")

    def parse_text(self, pages, filename=""):
        lines = list(iter_lines(pages))
        text = "\n".join(l for _, l in lines)
        stmt = Statement(bank=self.bank)
        printed = extract_card_last4_from_text(text)
        # SBI prints only the last two card digits; the filename carries the account number,
        # which cards.pdf_card_last4 maps to the card. Prefer a full 4-digit identifier.
        from_name = extract_card_last4_from_filename(filename)
        stmt.card_last4 = printed if len(printed) >= 4 else (from_name or printed)
        stmt.card_suffix = printed if len(printed) < 4 else ""
        self._parse_summary(stmt, lines, text)

        in_table = False
        last_date = None
        consumed = set()
        for pos, (idx, line) in enumerate(lines):
            if self.TABLE_START_RE.search(line):
                in_table = True
                continue
            if self.TABLE_END_RE.search(line):
                in_table = False
                continue
            if pos in consumed:
                continue
            m = self.DATED_RE.match(line)
            if m:
                date_str, desc, amt, code = m.groups()
                last_date = date_str
                desc = desc.strip() or self._wrapped_description(lines, pos, consumed)
            elif in_table and last_date:
                # charges printed under the line they belong to, without a date of their own
                m = self.UNDATED_RE.match(line)
                if not m or self._wraps_next_date_line(lines, pos):
                    continue
                desc, amt, code = m.groups()
                date_str = last_date
            else:
                continue
            amount = parse_amount(amt)
            if not amount:
                continue
            txn = Transaction(date=date_str, description=desc, amount=amount,
                              is_credit=code in ("C", "T"), card_last4=stmt.card_last4, line_index=idx)
            if code == "M":
                # Monthly instalment of a purchase already billed in full (Flexipay / merchant EMI)
                txn.pattern_kind = "emi_principal"
            fx = self.FOREIGN_RE.search(" " + txn.description)
            if fx:
                txn.original_amount, txn.currency = parse_amount(fx.group(1)), fx.group(2)
                txn.description = self.FOREIGN_RE.sub("", " " + txn.description, count=1).strip()
            (stmt.payments if is_payment(txn.description) else stmt.transactions).append(txn)
        return stmt

    def _wraps_next_date_line(self, lines, pos):
        """Is this undated line the first half of a wrapped description (the date line that
        follows has no text)? Then it belongs to that line, not to the previous date."""
        if pos + 1 >= len(lines):
            return False
        m = self.DATED_RE.match(lines[pos + 1][1])
        return bool(m and not m.group(2).strip())

    def _wrapped_description(self, lines, pos, consumed):
        parts = []
        if pos > 0 and (pos - 1) not in consumed:
            prev = lines[pos - 1][1]
            if prev and not self.DATED_RE.match(prev) and not self.TABLE_START_RE.search(prev):
                parts.append(prev)
                consumed.add(pos - 1)
        if pos + 1 < len(lines):
            nxt = lines[pos + 1][1]
            opened = parts and parts[0].count("(") > parts[0].count(")")
            if opened and nxt.endswith(")") and not self.DATED_RE.match(nxt):
                parts.append(nxt)
                consumed.add(pos + 1)
        return " ".join(parts)

    def _parse_summary(self, stmt, lines, text):
        m = re.search(r"Statement Period:\s*(\d{2} [A-Za-z]{3} \d{2,4})\s+to\s+(\d{2} [A-Za-z]{3} \d{2,4})", text)
        if m:
            stmt.period_start, stmt.period_end = parse_date_text(m.group(1)), parse_date_text(m.group(2))
        m = re.search(r"Total Amount Due\s*\( ` \)\s*\n\s*(-?[\d,]+\.\d{2})(\s*CR)?", text)
        if m:
            stmt.total_due = parse_amount(m.group(1)) * (-1 if m.group(2) else 1)
        for pos, (_, line) in enumerate(lines):
            if line.startswith("**Minimum Amount Due"):
                # the figure lands at the end of one of the next lines ("CKYC No. : ... 5,598.00")
                for _, nxt in lines[pos + 1:pos + 4]:
                    mm = re.search(r"(?:^|\s)(-?[\d,]+\.\d{2})$", nxt)
                    if mm:
                        stmt.min_due = parse_amount(mm.group(1))
                        break
            elif line.endswith("Statement Date"):
                # the value line can be pushed down by a wrapped ")" line
                for _, nxt in lines[pos + 1:pos + 3]:
                    mm = re.search(r"(\d{2} [A-Za-z]{3} \d{4})$", nxt)
                    if mm:
                        stmt.statement_date = parse_date_text(mm.group(1))
                        break
            elif line.endswith("Payment Due Date"):
                # "NO PAYMENT REQUIRED" when nothing is due: no due date
                for _, nxt in lines[pos + 1:pos + 3]:
                    mm = re.search(r"(\d{2} [A-Za-z]{3} \d{4})$", nxt)
                    if mm:
                        stmt.due_date = parse_date_text(mm.group(1))
                        break
            elif line.startswith("( ` ) Credits ( ` )") and pos + 1 < len(lines):
                # ACCOUNT SUMMARY: previous, payments & credits, purchases, fees/interest, outstanding;
                # a credit balance prints as "540.16 CR"
                amt = r"(-?[\d,]+\.\d{2})(\s*CR)?"
                mm = re.match(r"^" + r"\s+".join([amt] * 5) + r"$", lines[pos + 1][1])
                if mm:
                    g = mm.groups()
                    prev, pay, purch, fees, _out = (parse_amount(g[i]) * (-1 if g[i + 1] else 1)
                                                    for i in range(0, 10, 2))
                    stmt.previous_balance, stmt.payments_credits, stmt.purchases = prev, pay, purch + fees
        if stmt.statement_date is None:
            stmt.statement_date = stmt.period_end
