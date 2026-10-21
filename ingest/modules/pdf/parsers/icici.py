import re

from .base import (Statement, StatementParser, Transaction, extract_card_last4_from_filename,
                   is_payment, iter_lines, parse_amount, parse_date_text)


class ICICIParser(StatementParser):
    """Parse ICICI Bank credit card statements (Amazon Pay, Sapphiro).

    Line layout:  Date  SerNo  Description  RewardPoints  [Intl amount]  Amount(INR)  [CR]
        21/07/2026 10000000001 CITY FUEL STATION KRISHNA IN 0 505.69
        22/07/2026 10000000002 Fuel Surcharges 0 5.00 CR
        02/03/2026 10000000003 PROTON AG* PROTON AG GENEVA CH* 261 71.76 6,805.69   (next line: USD)

    Why the old parser dropped rows (iter-18 M3, Aug-2026 Amazon Pay statement: bank
    Rs 1,08,766.63 vs parsed Rs 23,884.76):
      - The spends-overview pie chart is laid out beside the table, so its labels are glued in
        front of transaction lines ("2% 25/07/2026 ... CMR TEXTILES ... 754 75,400.00",
        "59% 13/09/2026 ... Principal Amount Amortization - 0 4,786.90"). A line-start anchor
        missed every such row: CMR Rs 75,400 / Rs 1,000, Amazon Rs 7,995, the Sep EMI line.
      - "CR" lines never matched, so credits were lost and payments were never recognised.
      - International lines carry a second amount; the foreign amount was stored as rupees.
    Sapphiro statements list each card on the account under its own header
    ("3747XXXXXXXX1234" for the Amex add-on, "5241XXXXXXXX5678"); each line keeps the card it was
    made on.
    """
    bank = "icici"

    TXN_RE = re.compile(
        r"(\d{2}/\d{2}/\d{4})\s+(\d{8,})\s+(.*?)\s+(-?\d+)\s+"   # date, serial, desc, reward points
        r"(?:([\d,]+\.\d{2})\s+)?([\d,]+\.\d{2})(\s*CR)?\s*$"     # [intl amount], INR amount, [CR]
    )
    SECTION_RE = re.compile(r"^\d{4}X{6,10}(\d{4})$")
    EMI_PLAN_RE = re.compile(r"(<\d+/\d+>.*)$")        # "<6/6>AMAZON PAY INDIA PVT LT"
    CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
    DATE_WORDS = r"([A-Z][a-z]+ \d{1,2}, \d{4})"

    def parse_text(self, pages, filename=""):
        lines = list(iter_lines(pages))
        text = "\n".join(l for _, l in lines)
        stmt = Statement(bank=self.bank)
        sections = [m.group(1) for _, l in lines for m in [self.SECTION_RE.match(l)] if m]
        # The filename names the account's primary card; an uploaded file has no such name, and
        # the primary card's section comes last on the statement.
        stmt.card_last4 = extract_card_last4_from_filename(filename) or (sections[-1] if sections else "")
        self._parse_summary(stmt, text)

        section_card = stmt.card_last4
        rows = []
        for pos, (idx, line) in enumerate(lines):
            sec = self.SECTION_RE.match(line)
            if sec:
                section_card = sec.group(1)
                continue
            m = self.TXN_RE.search(line)
            if not m:
                continue
            date_str, _serial, desc, _points, intl, amt, cr = m.groups()
            amount = parse_amount(amt)
            if not amount:
                continue                      # "0.00 CR" placeholder rows
            desc = desc.strip()
            nxt = lines[pos + 1][1] if pos + 1 < len(lines) else ""
            plan = self.EMI_PLAN_RE.search(nxt) if not self.TXN_RE.search(nxt) else None
            if plan and "Amortization" in desc:
                desc = f"{desc} {plan.group(1).strip()}"
            txn = Transaction(date=date_str, description=desc, amount=amount, is_credit=bool(cr),
                              card_last4=section_card, line_index=idx)
            if intl:
                txn.original_amount = parse_amount(intl)
                if self.CURRENCY_RE.match(nxt):
                    txn.currency = nxt
            rows.append(txn)

        self._mark_emi_conversions(rows)
        for txn in rows:
            (stmt.payments if is_payment(txn.description) else stmt.transactions).append(txn)
        return stmt

    def _mark_emi_conversions(self, rows):
        """A purchase converted to EMI shows as the debit, an equal CR on the same merchant and a
        new "<1/N>MERCHANT" amortisation plan. That CR is the conversion, not a refund: kind
        emi_conversion keeps it out of the refunds netted against spend (contract 4)."""
        new_plans = [r.description.upper() for r in rows if not r.is_credit and "<1/" in r.description]
        if not new_plans:
            return
        debits = {(r.description.upper(), r.amount) for r in rows if not r.is_credit}
        for r in rows:
            if not r.is_credit or (r.description.upper(), r.amount) not in debits:
                continue
            head = r.description.upper().split()[0] if r.description.split() else ""
            if head and any(head in p for p in new_plans):
                r.pattern_kind = "emi_conversion"

    def _parse_summary(self, stmt, text):
        m = re.search(r"Statement period\s*:\s*" + self.DATE_WORDS + r"\s+to\s+" + self.DATE_WORDS, text)
        if m:
            stmt.period_start, stmt.period_end = parse_date_text(m.group(1)), parse_date_text(m.group(2))
        # Headings are sometimes extracted with doubled letters ("SSTTAATTEEMMEENNTT DDAATTEE").
        m = re.search(r"(?:STATEMENT DATE|SSTTAATTEEMMEENNTT DDAATTEE)\s*\n\s*" + self.DATE_WORDS, text)
        stmt.statement_date = parse_date_text(m.group(1)) if m else stmt.period_end
        m = re.search(r"(?:PAYMENT DUE DATE|PPAAYYMMEENNTT DDUUEE DDAATTEE)\s*\n\s*" + self.DATE_WORDS, text)
        if m:
            stmt.due_date = parse_date_text(m.group(1))
        m = re.search(r"Total Amount due\s*\n\s*`?\s*(-?[\d,]+\.\d{2})(\s*CR)?", text)
        if m:
            stmt.total_due = parse_amount(m.group(1)) * (-1 if m.group(2) else 1)
        m = re.search(r"Minimum Amount due[^\n]*\n\s*`?\s*(-?[\d,]+\.\d{2})", text)
        if m:
            stmt.min_due = parse_amount(m.group(1))
        # "Previous Balance Purchases / Charges Cash Advances Payments / Credits"
        # "`16,594.97 `1,08,785.45 `0.00 `16,613.79"   (a credit balance prints as "`6,399.78 CR")
        amt = r"`?(-?[\d,]+\.\d{2})(\s*CR)?"
        m = re.search(r"Previous Balance\s+Purchases / Charges\s+Cash Advances\s+Payments / Credits\s*\n\s*"
                      + r"\s+".join([amt] * 4), text)
        if m:
            g = m.groups()
            prev, purch, cash, pay = (parse_amount(g[i]) * (-1 if g[i + 1] else 1) for i in range(0, 8, 2))
            stmt.previous_balance, stmt.purchases, stmt.payments_credits = prev, purch + cash, pay
