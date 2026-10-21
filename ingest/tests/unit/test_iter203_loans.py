"""Loan EMIs paid by own-account transfer are Loan Repayment, not Self Transfer — iteration 203 E7.

SBI words the monthly EMI as "Debited INR 75,000.00 -Transferred to Mr. <owner>" (an internal
transfer to the owner's own loan account), which the owner-name alias files as Self Transfer and
excludes everywhere. The loan account's own SMS ("Your loan A/C XXXXX7777 Credited INR 75,000.00 ...
deposit transfer from Mr. <owner>") or the standing-instruction SMS ("... to Loan A/c No.XXXXX7777")
proves where the money went. All names, accounts and amounts here are made up."""
from modules.pairing import pair_rows
from modules.txn_store import insert_transaction
from routes.analytics import analytics_overview

_ref = iter(range(5000, 9999))


def own_debit(conn, amount, date, desc="Transferred to Mr. TESTOWNER", card="4321", source="email", **kw):
    """An own-name transfer as the categoriser files it today (Self Transfer)."""
    return insert_transaction(conn, amount=amount, date=date, source=source, source_ref=str(next(_ref)),
                              raw_description=desc, card_last4=card, context="bank",
                              merchant="Self Transfer", category="Self Transfer", confidence="high", **kw)["id"]


def loan_credit(conn, amount, date, loan="7777", day_text=None):
    d = day_text or f"{date[8:10]}/{date[5:7]}/{date[2:4]}"
    text = (f"Dear Customer, Your loan A/C XXXXX{loan} Credited INR {amount:,.2f} on {d},"
            f"deposit transfer from Mr. TESTOWNER.-SBI. Download YONO")
    return insert_transaction(conn, amount=amount, date=date, source="sms", source_ref=str(next(_ref)),
                              raw_description="deposit transfer from Mr. TESTOWNER", raw_text=text,
                              card_last4=loan, txn_type="credit", context="bank",
                              merchant="Self Transfer", category="Self Transfer", confidence="high")["id"]


def si_debit(conn, amount, date, loan="7777", card="4321"):
    """The standing-instruction SMS the parser already files as Loan Repayment."""
    text = (f"Dear Customer, Standing Instruction has been successfully executed for Rs. {amount:,.2f} "
            f"from A/c No.XXXXX{card} to Loan A/c No.XXXXX{loan} on {date[8:10]}/{date[5:7]}/{date[2:4]}.  - SBI.")
    return insert_transaction(conn, amount=amount, date=date, source="sms", source_ref=str(next(_ref)),
                              raw_description=f"Loan repayment (EMI) - Standing instruction to Loan A/c No.XXXXX{loan}",
                              raw_text=text, card_last4=card, context="bank")["id"]


def cat(conn, tid):
    return conn.execute("SELECT category FROM transactions WHERE id = ?", (tid,)).fetchone()["category"]


def _own(make_card):
    make_card("SBI", "4321", name="Savings account", active=0)
    make_card("HSBC", "001", name="Account 001", active=0)


def test_own_transfer_paired_with_a_loan_credit_is_a_loan_repayment(conn, make_card):
    _own(make_card)
    d = own_debit(conn, 75000, "2026-06-22")
    c = loan_credit(conn, 75000, "2026-06-22")
    assert analytics_overview(period="2026-06")["outflows"]["loans"]["total"] == 0
    res = pair_rows(conn)
    assert res["loan_repayments"] == 1 and res["loan_repayment_amount"] == 75000
    assert cat(conn, d) == cat(conn, c) == "Loan Repayment"
    o = analytics_overview(period="2026-06")
    assert o["outflows"]["loans"] == {"total": 75000, "count": 1}       # the credit side counts nowhere
    assert o["total"] == 0 and o["outflows"]["sent_to_people"]["total"] == 0
    assert pair_rows(conn)["rows_changed"] == 0                          # idempotent


def test_loan_credit_up_to_two_days_away_pairs(conn, make_card):
    _own(make_card)
    d = own_debit(conn, 75000, "2026-07-20")
    loan_credit(conn, 75000, "2026-07-22")
    pair_rows(conn)
    assert cat(conn, d) == "Loan Repayment"


def test_different_amount_or_more_than_two_days_does_not_pair(conn, make_card):
    _own(make_card)
    far = own_debit(conn, 75000, "2026-06-01")
    loan_credit(conn, 75000, "2026-06-04")
    other = own_debit(conn, 45000, "2026-07-22")
    loan_credit(conn, 44999, "2026-07-22")
    res = pair_rows(conn)
    assert res["loan_repayments"] == 0
    assert cat(conn, far) == cat(conn, other) == "Self Transfer"


def test_unpaired_own_transfer_stays_self_transfer(conn, make_card):
    _own(make_card)
    d = own_debit(conn, 60000, "2026-05-19")
    pair_rows(conn)
    assert cat(conn, d) == "Self Transfer"


def test_ambiguous_loan_pair_pairs_nothing(conn, make_card):
    _own(make_card)
    a = own_debit(conn, 75000, "2026-02-24")
    b = own_debit(conn, 75000, "2026-02-24", source="sms", link=False)      # a second, genuine transfer
    loan_credit(conn, 75000, "2026-02-24")
    assert pair_rows(conn)["loan_repayments"] == 0
    assert cat(conn, a) == cat(conn, b) == "Self Transfer"


def test_an_emi_amount_confirmed_on_the_account_marks_its_other_months(conn, make_card):
    """The SI SMS proves Rs 40,000 from this account goes to a loan; the months SBI words it as
    'Transferred to Mr. <owner>' (no loan SMS) are the same EMI."""
    _own(make_card)
    si = si_debit(conn, 40000, "2026-08-25")
    assert cat(conn, si) == "Loan Repayment"
    months = [own_debit(conn, 40000, d) for d in ("2026-05-25", "2026-06-26", "2026-09-25")]
    near_miss = own_debit(conn, 44000, "2026-07-28")
    other_account = own_debit(conn, 40000, "2026-07-28", card="001")
    named_own_account = own_debit(conn, 40000, "2026-04-26", desc="NEFT via Online to SBOI A/c ***4321 Owner SBI",
                                  card="001")
    pair_rows(conn)
    assert [cat(conn, m) for m in months] == ["Loan Repayment"] * 3
    assert cat(conn, near_miss) == cat(conn, other_account) == cat(conn, named_own_account) == "Self Transfer"
    assert analytics_overview(period="2026-06")["outflows"]["loans"]["total"] == 40000
    assert pair_rows(conn)["rows_changed"] == 0


def test_a_debit_naming_a_known_loan_account_is_a_loan_repayment(conn, make_card):
    _own(make_card)
    loan_credit(conn, 45000, "2026-01-25", loan="0377")             # proves ...0377 is a loan account
    d = own_debit(conn, 45000, "2026-03-25", desc="SI Fund Transfer to . Ac X0377", source="sms")
    unrelated = own_debit(conn, 45000, "2026-03-25", desc="SI Fund Transfer to . Ac X0399", source="sms")
    pair_rows(conn)
    assert cat(conn, d) == "Loan Repayment"
    assert cat(conn, unrelated) == "Self Transfer"


def test_loan_pairing_never_touches_a_hand_labelled_row(conn, make_card):
    _own(make_card)
    d = own_debit(conn, 75000, "2026-06-22")
    loan_credit(conn, 75000, "2026-06-22")
    conn.execute("UPDATE transactions SET confidence = 'manual' WHERE id = ?", (d,))
    conn.commit()
    pair_rows(conn)
    assert cat(conn, d) == "Self Transfer"
