"""iter-18 statement parsers (M2, M3, H8, HP5) — from anonymised extracted text, no PDFs.

Fixtures under fixtures/pdf/ are pdfplumber's text of real statement layouts with names, card
and account numbers and references replaced; amounts and line shapes are as printed."""
import os
import re
from datetime import date

import pytest

from modules.pdf.parsers import get_parser
from modules.pdf.parsers.base import is_payment, normalize_date, parse_date_text

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "pdf")


def pages(name):
    text = open(os.path.join(FIX, name), encoding="utf-8").read()
    return re.split(r"(?m)^=== PAGE \d+ ===\n", text)[1:]


def parse(bank, fixture, filename="statement.pdf"):
    return get_parser(bank).parse_text(pages(fixture), filename=filename)


def split(stmt):
    debits = sum(t.amount for t in stmt.transactions if not t.is_credit)
    credits = sum(t.amount for t in stmt.transactions if t.is_credit)
    paid = sum(t.amount for t in stmt.payments if t.is_credit) - sum(t.amount for t in stmt.payments if not t.is_credit)
    return round(debits, 2), round(credits, 2), round(paid, 2)


# --- M2: HDFC payment lines wrap around the date line ----------------------------------------

def test_hdfc_wrapped_payment_is_a_payment_not_a_debit_described_plus():
    st = parse("hdfc", "hdfc_2026_wrapped_payment.txt")
    assert all(t.description != "+" for t in st.transactions + st.payments)
    assert len(st.payments) == 1
    pay = st.payments[0]
    assert pay.is_credit and pay.amount == 15336.0 and pay.date == "2026-06-20"
    assert pay.description.startswith("BPPY CC PAYMENT") and pay.description.endswith("ST000000000000000000001)")
    assert not any(t.amount == 15336.0 for t in st.transactions)


def test_hdfc_neucoins_column_is_not_a_credit_marker_and_cashback_stays_a_credit():
    st = parse("hdfc", "hdfc_2026_wrapped_payment.txt")
    by_amount = {t.amount: t for t in st.transactions}
    emi = by_amount[2826.0]
    assert not emi.is_credit and emi.description == "EMI 1MGHEALTHCARESOLUTIO"
    cashback = by_amount[45.20]
    assert cashback.is_credit and "CashBack" in cashback.description
    igst = by_amount[59.31]                      # wrapped, no '+': a debit with its text rebuilt
    assert not igst.is_credit and igst.description.startswith("IGST-VPS0000000000001")


def test_hdfc_identical_lines_stay_two_transactions():
    st = parse("hdfc", "hdfc_2026_wrapped_payment.txt")
    twins = [t for t in st.transactions if t.amount == 686.70]
    assert len(twins) == 2 and twins[0].line_index != twins[1].line_index


def test_hdfc_printed_bill_and_reconciliation():
    st = parse("hdfc", "hdfc_2026_wrapped_payment.txt")
    assert st.card_last4 == "1111"
    assert (st.total_due, st.min_due, st.due_date) == (14537.09, 730.0, date(2026, 7, 21))
    assert (st.statement_date, st.period_start, st.period_end) == (date(2026, 7, 1), date(2026, 6, 2), date(2026, 7, 1))
    debits, credits, paid = split(st)
    assert debits == st.purchases == 14582.55
    assert round(credits + paid, 2) == st.payments_credits
    assert round(st.previous_balance - paid + debits - credits, 2) == st.total_due


def test_hdfc_nothing_due_prints_nil_and_has_no_due_date():
    text = pages("hdfc_2026_wrapped_payment.txt")
    text[0] = text[0].replace("C730.00 21 Jul, 2026", "C0.00 Nil").replace("_ C14,537.09", "_ C-0.14")
    st = get_parser("hdfc").parse_text(text)
    assert st.due_date is None and st.total_due == -0.14 and st.min_due == 0.0


def test_hdfc_2025_layout():
    st = parse("hdfc", "hdfc_2025_layout.txt")
    assert st.card_last4 == "1111"                       # "Card No: 6529 26XX XXXX 1111"
    assert (st.total_due, st.min_due, st.due_date, st.statement_date) == (9053.0, 460.0, date(2025, 9, 21), date(2025, 9, 1))
    assert [t.description for t in st.payments] == ["TELE TRANSFER CREDIT (Ref# ST000000000000000000002)"]
    assert st.transactions[0].description == "UPI-PIZZA STUDIO" and st.transactions[0].date == "2025-08-08"
    debits, credits, paid = split(st)
    assert debits == 9053.43 and credits == 0 and paid == 8630.0


# --- M3: ICICI rows behind the pie-chart labels, CR lines, payments ---------------------------

def test_icici_reconciles_with_the_printed_total():
    st = parse("icici", "icici_amazon_2026_08.txt", "4315XXXXXXXX2222_100001_Retail_Amazon_NORM.pdf")
    debits, credits, paid = split(st)
    assert debits == st.purchases == 108785.45           # old parser: 23,884.76
    assert credits == 18.82 and paid == 16594.97
    assert round(credits + paid, 2) == st.payments_credits
    assert round(debits - credits, 2) == st.total_due == 108766.63      # previous balance paid in full
    amounts = [t.amount for t in st.transactions]
    assert 75400.0 in amounts and 7995.0 in amounts and 1000.0 in amounts and 4723.96 in amounts


def test_icici_line_details():
    st = parse("icici", "icici_amazon_2026_08.txt", "4315XXXXXXXX2222_100001_Retail_Amazon_NORM.pdf")
    assert st.card_last4 == "2222"
    assert (st.statement_date, st.due_date, st.min_due) == (date(2026, 8, 20), date(2026, 9, 7), 10070.0)
    cmr = [t for t in st.transactions if t.amount == 75400.0][0]
    assert cmr.description == "CMR TEXTILES AND JEWEL VIJAYAWADA IN" and cmr.date == "2026-07-25"   # no points
    principal = [t for t in st.transactions if t.amount == 4723.96][0]
    assert principal.description == "Principal Amount Amortization - <5/6>AMAZON PAY INDIA PVT LT"
    fuel = [t for t in st.transactions if t.description == "Fuel Surcharges"]
    assert len(fuel) == 4 and all(t.is_credit for t in fuel)
    assert [p.description for p in st.payments] == ["BBPS Payment received"]


def test_icici_sapphiro_sections_foreign_line_emi_conversion_and_cr_balances():
    st = parse("icici", "icici_sapphiro_addon.txt", "5241XXXXXXXX8888_200002_Retail_Sapphiro_NORM.pdf")
    assert st.card_last4 == "8888"
    assert st.total_due == -7280.91 and st.previous_balance == -11677.03
    proton = [t for t in st.transactions if "PROTON" in t.description][0]
    assert proton.card_last4 == "3333" and proton.amount == 6805.69          # INR, not the USD figure
    assert (proton.currency, proton.original_amount) == ("USD", 71.76)
    fee = [t for t in st.transactions if t.description == "Late Payment Fee"][0]
    assert fee.card_last4 == "0000"                     # unknown section; the store maps it to 8888
    conversion = [t for t in st.transactions if t.is_credit and t.amount == 18619.0][0]
    assert conversion.pattern_kind == "emi_conversion"
    assert not any(t.amount == 0 for t in st.transactions)
    debits, credits, paid = split(st)
    assert debits == st.purchases and round(credits + paid, 2) == st.payments_credits
    assert round(st.previous_balance - paid + debits - credits, 2) == st.total_due


# --- SBI: wrapped lines, 'M' instalments, undated charges, bounced payments --------------------

def test_sbi_lines_the_old_parser_dropped():
    st = parse("sbi", "sbi_2026_09.txt", "1234567890125555_19092026.pdf")
    by_desc = {t.description: t for t in st.transactions}
    claude = by_desc["ANTHROPIC* CLAUDE SUB ANTHROPIC.COM CA (Pay in EMIs)"]
    assert (claude.amount, claude.date, claude.currency, claude.original_amount) == (11299.11, "2026-08-24", "USD", 118.0)
    emi = by_desc["FP EMI 02/06(EXCL TAX 56.03)"]
    assert emi.pattern_kind == "emi_principal" and not emi.is_credit
    markup = by_desc["FORGN CURR MARKUP DB (EXCL TAX 71.18)"]
    assert markup.date == "2026-09-19" and markup.amount == 395.47
    assert by_desc["IGST DB @ 18.00%"].date == "2026-09-19"
    assert by_desc["CARD CASHBACK CREDIT"].is_credit                    # kept: it reduces the bill
    assert len([t for t in st.transactions if t.description == "BLINKIT GURGAON IN"]) == 2
    assert {p.description for p in st.payments} == {
        "PAYMENT RECEIVED 000DP000000000000AbCdEf", "DIRECT DEBIT PAYMENT(ACH)", "INSUFFICIENT FUNDS"}
    assert "PAYMENT DISHONOUR FEE" in by_desc                            # the fee is a real charge


def test_sbi_printed_bill_and_card_identifiers():
    st = parse("sbi", "sbi_2026_09.txt", "1234567890125555_19092026.pdf")
    assert (st.card_last4, st.card_suffix) == ("5555", "44")
    assert (st.total_due, st.min_due, st.due_date, st.statement_date) == (46088.0, 5598.0, date(2026, 10, 9), date(2026, 9, 19))
    assert (st.period_start, st.period_end) == (date(2026, 8, 20), date(2026, 9, 19))
    debits, credits, paid = split(st)
    emi = sum(t.amount for t in st.transactions if t.pattern_kind == "emi_principal")
    bounced = sum(t.amount for t in st.payments if not t.is_credit)
    # 'Purchases & Other Debits' + 'Fee, Taxes & Interest' = every debit except the EMI instalment
    assert round(debits - emi + bounced, 2) == st.purchases
    assert round(credits + paid + bounced, 2) == st.payments_credits    # the ACH credit + its bounce


# --- H8: the year comes from the statement, never a constant -----------------------------------

def test_hsbc_dec_jan_statement_dates():
    st = parse("hsbc", "hsbc_dec_jan_2027.txt", "20270104.pdf")
    assert (st.period_start, st.period_end) == (date(2026, 12, 5), date(2027, 1, 4))
    dates = {(t.description, t.is_credit): t.date for t in st.transactions}
    assert dates[("IAP MAKEMYTRIP INDIA PRIV GURGAON", False)] == "2026-12-28"
    assert dates[("IAP MYNTRA DESIGNS PRIVATE Bangalore", True)] == "2026-12-31"
    assert dates[("IAP MYNTRA DESIGNS PRIVATE Bangalore", False)] == "2027-01-02"
    assert {p.description for p in st.payments} == {"BBPS PMT BBPSDP0000000000000AbCd", "CREDIT CARD PAYMENT"}
    assert (st.total_due, st.min_due, st.due_date, st.card_last4) == (9866.94, 1060.39, date(2027, 1, 22), "7777")
    debits, credits, paid = split(st)
    assert debits == st.purchases and round(credits + paid, 2) == st.payments_credits


def test_hsbc_without_a_period_uses_today_not_2026(monkeypatch):
    import modules.pdf.parsers.base as base
    monkeypatch.setattr(base, "today_ist", lambda: date(2027, 1, 10))
    text = [p.replace("05 DEC 2026 To 04 JAN 2027", "") for p in pages("hsbc_dec_jan_2027.txt")]
    st = get_parser("hsbc").parse_text(text)
    dates = sorted(t.date for t in st.transactions)
    assert dates == ["2026-12-28", "2026-12-31", "2027-01-02", "2027-01-02"]


def test_year_less_dates_use_the_statement_date():
    assert normalize_date("21DEC", reference=date(2027, 1, 4)) == "2026-12-21"
    assert normalize_date("02JAN", reference=date(2027, 1, 4)) == "2027-01-02"
    assert normalize_date("15 MAR", reference=date(2027, 4, 4)) == "2027-03-15"
    assert normalize_date("20/08/2026") == "2026-08-20"
    assert parse_date_text("August 20, 2026") == date(2026, 8, 20)
    assert parse_date_text("19 Sep 26") == date(2026, 9, 19)
    assert parse_date_text("01 Jul, 2026") == date(2026, 7, 1)


# --- Axis --------------------------------------------------------------------------------------

def test_axis_credit_balance_bill():
    st = parse("axis", "axis_credit_balance.txt", "Credit Card Statement_5.pdf")
    assert st.card_last4 == "6666"
    assert (st.total_due, st.min_due, st.due_date, st.statement_date) == (-199.0, 0.0, date(2026, 1, 1), date(2025, 12, 12))
    assert [t.amount for t in st.transactions] == [199.0] and st.transactions[0].is_credit
    assert [p.amount for p in st.payments] == [799.0]
    assert (st.previous_balance, st.payments_credits, st.purchases) == (799.0, 998.0, 0.0)


@pytest.mark.parametrize("text,expected", [
    ("BPPY CC PAYMENT DP000000000001AbCdE (Ref# ST0000)", True),
    ("BPPY PYMT DP000000000002AbCdE (Ref# 000000000000000)", True),
    ("BBPS Payment received", True),
    ("PAYMENT RECEIVED 000DP000000000003AbCdE", True),
    ("CREDIT CARD PAYMENT", True),
    ("DIRECT DEBIT PAYMENT(ACH)", True),
    ("INSUFFICIENT FUNDS", True),
    ("Razorpay PaymentsBENGALURU", False),
    ("AIRTEL PAYMENTS BANK L,GURGAON UTILITIES", False),
    ("PAYMENT DISHONOUR FEE", False),
    ("10% Swiggy CashBack", False),
])
def test_payment_detection(text, expected):
    assert is_payment(text) is expected
