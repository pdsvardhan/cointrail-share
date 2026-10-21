"""Rows that belong together without being copies (modules.pairing) — iter-18 verifier, 1442."""
from modules.pairing import pair_rows
from modules.txn_store import insert_transaction
from routes.analytics import analytics_overview

_ref = iter(range(1000, 9999))


def ins(conn, amount, date, desc, card, txn_type="debit", context="bank", **kw):
    return insert_transaction(conn, amount=amount, date=date, source="sms", source_ref=str(next(_ref)),
                              raw_description=desc, card_last4=card, txn_type=txn_type, context=context, **kw)["id"]


def row(conn, tid):
    return conn.execute("SELECT * FROM transactions WHERE id = ?", (tid,)).fetchone()


def _hsbc(make_card):
    make_card("HSBC", "4706", name="Account", active=0)
    make_card("HSBC", "001", name="Account 001", active=0)
    make_card("HSBC", "8938", name="Platinum", statement_day=15, due_day=5)


def test_bare_debit_and_credit_on_two_own_accounts_are_a_self_transfer(conn, make_card):
    """HSBC account A 'debited ... as per your request' + account B 'credited ... as per the transaction'
    the same day: the owner's own money, not "Sent to people"."""
    _hsbc(make_card)
    d = ins(conn, 400000, "2025-12-10", "per your request", "706")
    c = ins(conn, 400000, "2025-12-10", "per the transaction", "001", txn_type="credit")
    assert row(conn, d)["category"] == "Transfer"
    res = pair_rows(conn)
    assert res["own_transfers"] == 1
    assert row(conn, d)["category"] == row(conn, c)["category"] == "Self Transfer"
    assert analytics_overview(period="2025-12")["outflows"]["sent_to_people"]["total"] == 0
    assert pair_rows(conn)["rows_changed"] == 0                        # idempotent


def test_same_account_and_named_payees_are_never_paired(conn, make_card):
    _hsbc(make_card)
    same = ins(conn, 45000, "2025-10-10", "DEBIT", "706")
    ins(conn, 45000, "2025-10-10", "CREDIT", "706", txn_type="credit")       # came back to the same account
    named = ins(conn, 16000, "2025-12-10", "RAVI KUMAR", "706", context="upi")
    ins(conn, 16000, "2025-12-10", "per the transaction", "001", txn_type="credit")
    pair_rows(conn)
    assert row(conn, same)["category"] == "Transfer" and row(conn, named)["category"] != "Self Transfer"


def test_an_ambiguous_pair_pairs_nothing(conn, make_card):
    _hsbc(make_card)
    make_card("SBI", "4321", name="Savings account", active=0)
    d = ins(conn, 20000, "2025-12-10", "per your request", "706")
    ins(conn, 20000, "2025-12-10", "per the transaction", "001", txn_type="credit")
    ins(conn, 20000, "2025-12-10", "CREDIT", "4321", txn_type="credit")
    assert pair_rows(conn)["own_transfers"] == 0 and row(conn, d)["category"] == "Transfer"


def test_bare_account_debit_paying_an_own_card_is_a_card_bill(conn, make_card):
    _hsbc(make_card)
    d = ins(conn, 5236.99, "2025-08-22", "DEBIT", "706")
    insert_transaction(conn, amount=5236.99, date="2025-08-22", source="card", source_ref="st#1",
                       raw_description="CREDIT CARD PAYMENT", card_last4="8938", txn_type="credit", context="card")
    res = pair_rows(conn)
    assert res["card_autopays"] == 1
    r = row(conn, d)
    assert (r["category"], r["kind"]) == ("Finance", "bill_payment")
    assert analytics_overview(period="2025-08")["outflows"]["sent_to_people"]["total"] == 0


def test_a_reversal_nets_where_the_failed_payment_was_counted(conn, make_card):
    """Four failed Rs 280 UPI payments came back as 'UPI reversal' credits carrying their refs:
    'Sent to people' must not keep them; an ATM reversal reduces the ATM withdrawal."""
    make_card("HDFC", "4400", name="Account", active=0)
    for i in range(2):
        ins(conn, 280, "2025-06-29", "RAMESH", "4400", context="upi", ref_no=f"30000000000{i}")
    ins(conn, 280, "2025-06-29", "HDFC UPI reversal", "", txn_type="credit", context="upi",
        ref_no="300000000000", pattern_kind="reversal")
    atm = ins(conn, 2500, "2025-06-29", "HDFC ATM", "4400", context="atm", ref_no="500000000001")
    rev = ins(conn, 2500, "2025-06-29", "HDFC UPI reversal", "", txn_type="credit", context="upi",
              ref_no="500000000001", pattern_kind="reversal")
    assert row(conn, atm)["category"] == "ATM Withdrawal" and row(conn, rev)["category"] != "ATM Withdrawal"
    before = analytics_overview(period="2025-06")
    assert before["outflows"]["sent_to_people"]["total"] == 560 and before["total"] == 2500
    assert pair_rows(conn)["reversals_aligned"] == 2
    after = analytics_overview(period="2025-06")
    assert after["outflows"]["sent_to_people"] == {"total": 280, "count": 2}
    assert after["total"] == 0


def test_paired_labels_survive_recategorise(conn, make_card):
    from modules.categorizer import recategorize_with_context
    _hsbc(make_card)
    d = ins(conn, 15000, "2025-12-11", "per your request", "706")
    ins(conn, 15000, "2025-12-11", "per the transaction", "001", txn_type="credit")
    pair_rows(conn)
    recategorize_with_context(conn, dry_run=False)
    assert row(conn, d)["category"] == "Self Transfer"


def test_a_debit_that_bounced_back_the_same_day_nets_to_nothing(conn, make_card):
    """HSBC 'as DEBIT' Rs 45,000 leaving 'avl Bal 0.00', then 'as CREDIT' Rs 45,000 on the same
    account the same day: the debit came straight back and is not money sent anywhere."""
    _hsbc(make_card)
    d = ins(conn, 45000, "2025-10-10", "DEBIT", "706")
    c = ins(conn, 45000, "2025-10-10", "CREDIT", "706", txn_type="credit")
    res = pair_rows(conn)
    assert res["returned_debits"] == 1 and row(conn, c)["kind"] == "reversal"
    assert analytics_overview(period="2025-10")["outflows"]["sent_to_people"]["total"] == 0
    assert row(conn, d)["category"] == "Transfer" and pair_rows(conn)["rows_changed"] == 0


def test_one_reversal_per_debit(conn, make_card):
    make_card("HDFC", "4400", name="Account", active=0)
    ins(conn, 280, "2025-06-29", "RAMESH", "4400", context="upi", ref_no="300000000009")
    for _ in range(2):          # two live reversal credits (link=False: the ref rule would merge them)
        ins(conn, 280, "2025-06-29", "HDFC UPI reversal", "", txn_type="credit", context="upi",
            ref_no="300000000009", pattern_kind="reversal", link=False)
    assert conn.execute("SELECT COUNT(*) c FROM transactions WHERE txn_type='credit' AND is_duplicate=0").fetchone()["c"] == 2
    assert pair_rows(conn)["reversals_aligned"] == 1


def test_a_returned_credit_follows_the_owners_label_on_its_debit(conn, make_card):
    """The owner files the bounced Rs 45,000 as a loan payment: the credit that brought it back must
    cancel it there, not stay a 'Sent to people' reversal."""
    _hsbc(make_card)
    d = ins(conn, 45000, "2025-10-10", "DEBIT", "706")
    ins(conn, 45000, "2025-10-10", "CREDIT", "706", txn_type="credit")
    pair_rows(conn)
    conn.execute("UPDATE transactions SET category = 'Loan Repayment', confidence = 'manual' WHERE id = ?", (d,))
    conn.commit()
    pair_rows(conn)
    o = analytics_overview(period="2025-10")
    assert o["outflows"]["loans"]["total"] == 0 and o["outflows"]["sent_to_people"]["total"] == 0


def test_sbi_payeeless_auto_debit_paying_an_own_card_is_a_card_bill(conn, make_card):
    make_card("SBI", "4321", name="Savings account", active=0)
    make_card("SBI", "2971", name="Aurum", statement_day=15, due_day=5)
    d = insert_transaction(conn, amount=830.04, date="2026-02-08", source="email", source_ref="e1",
                           raw_description="SBI ACCOUNT TRANSACTION", card_last4="4321", context="bank")["id"]
    insert_transaction(conn, amount=830.04, date="2026-02-08", source="card", source_ref="st#2",
                       raw_description="DIRECT DEBIT PAYMENT(ACH)", card_last4="2971", txn_type="credit", context="card")
    assert pair_rows(conn)["card_autopays"] == 1
    assert (row(conn, d)["category"], row(conn, d)["kind"]) == ("Finance", "bill_payment")
