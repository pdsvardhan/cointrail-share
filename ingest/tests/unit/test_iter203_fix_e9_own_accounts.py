"""iter-203 E9 (verifier round 2) — an account payee rule names the COUNTERPARTY, never the owner.

Every bank SMS prints the owner's own account ("Your a/c no. XXXXXXXX1234 is debited", "From HDFC Bank
A/C *4888"). An account key offered for an IMPS to the owner's own account ('...888' when the owner's
HDFC account ends 4888) matched every message that prints that account — hundreds of unrelated rows
from one tick of "apply to all payments to this payee". Now: a description that names one of the
owner's own accounts names no payee, and an account rule only reads accounts in the counterparty
position of the message. Accounts and payees are made up."""
from modules import payee_rules
from routes.transactions import get_transaction_payee, update_transaction


def own_account(conn, bank, last4, name="Savings account"):
    conn.execute("INSERT INTO cards (bank, last4, card_name, active) VALUES (?, ?, ?, 0)", (bank, last4, name))
    conn.commit()


def row(conn, desc, raw_text, category="Transfer", amount=500.0, txn_type="debit"):
    cur = conn.execute(
        "INSERT INTO transactions (amount, raw_description, merchant, category, date, source, card_last4, "
        "txn_type, confidence, raw_text, is_duplicate) VALUES (?, ?, ?, ?, '2026-10-01', 'sms', '', ?, "
        "'medium', ?, 0)",
        (amount, desc, desc, category, txn_type, raw_text),
    )
    conn.commit()
    return cur.lastrowid


def sbi_imps(own_tail, to_tail, amount="61234.00"):
    return (f"Dear Customer, Your a/c no. XXXXXXXX{own_tail} is debited for Rs.{amount} on 03-02-26 and "
            f"a/c XXXXXXX{to_tail} credited (IMPS Ref no 600000000101).If not done by you, call 1800111109 -SBI")


def hdfc_sent(own_tail, payee):
    return (f"Sent Rs.500.00 From HDFC Bank A/C *{own_tail} To {payee} On 08/10/26 Ref 600000000017 "
            f"Not You? Call 18002586161/SMS BLOCK UPI to 7308080808")


def hsbc_credit(own_acct, from_acct):
    return (f"HSBC: Dear Customer, your HSBC A/c {own_acct} has been credited with INR 1,234.00+ on 11MAR as "
            f"transfer from A/c {from_acct} . Your available Bal is 1,000.00 .")


def test_an_imps_to_the_owners_own_account_offers_no_payee(conn):
    own_account(conn, "HDFC", "4888")
    own_account(conn, "SBI", "7777")
    txn = row(conn, "IMPS to a/c XXXXXXX888", sbi_imps("7777", "888"), category="Self Transfer")
    out = get_transaction_payee(txn)
    assert out["payee"] is None
    assert "your own account" in out["refused"]
    assert out["matching_transactions"] == 0


def test_a_description_naming_an_own_account_offers_no_name_rule_either(conn):
    own_account(conn, "SBI", "4321")
    txn = row(conn, "NEFT via Online to SBOI A/c ***4321 Test Owner SBI",
              "A/c 123-456***-002 is debited with INR 100.00- as NEFT via Online to SBOI A/c ***4321 Test Owner SBI .",
              category="Self Transfer")
    key_type, key, _label, refused = payee_rules.payee_key("NEFT via Online to SBOI A/c ***4321 Test Owner SBI", conn)
    assert key_type is None and "your own account" in refused
    assert get_transaction_payee(txn)["payee"] is None


def test_a_transfer_from_an_own_account_offers_no_payee(conn):
    own_account(conn, "HSBC", "007")
    own_account(conn, "HSBC", "002")
    txn = row(conn, "Self transfer (transfer from A/c 123-456***-007)",
              hsbc_credit("123-456***-002", "123-456***-007"), category="Self Transfer", txn_type="credit")
    assert get_transaction_payee(txn)["payee"] is None


def test_apply_to_payee_on_an_own_account_transfer_creates_no_rule(conn):
    own_account(conn, "HDFC", "4888")
    own_account(conn, "SBI", "7777")
    txn = row(conn, "IMPS to a/c XXXXXXX888", sbi_imps("7777", "888"), category="Self Transfer")
    everyday = [row(conn, f"PAYEE {i}", hdfc_sent("4888", f"PAYEE {i}")) for i in range(5)]
    res = update_transaction(txn, {"category": "Household help", "apply_to_payee": True})
    assert res["rule"] is None and "your own account" in res["rule_refused"]
    assert res["relabelled"] == 0
    cats = {r["id"]: r["category"] for r in conn.execute("SELECT id, category FROM transactions")}
    assert all(cats[i] == "Transfer" for i in everyday)


def test_an_account_rule_reads_only_the_counterparty_account_of_a_message(conn):
    # the owner's own account is NOT registered here: position alone must keep it out
    to_payee = row(conn, "IMPS to a/c XXXXXXX418", sbi_imps("1111", "418"))
    own_side = row(conn, "IMPS to a/c XXXXXXX222", sbi_imps("7418", "222"))      # owner's a/c ends 7418
    hdfc_own = row(conn, "PAYEE X", hdfc_sent("2418", "PAYEE X"))                # owner's a/c ends 2418
    yono = row(conn, "Shanth", "Your Ac x1418 debited Rs.3,21,000.00 for transfer to Shanth Ac x9418 dt 14.03.26 "
                               "Ref SBI000000000001. YONO SBI")
    _rule, changed = payee_rules.save_rule(conn, "account", "418", "a/c ...418", "Home Construction",
                                           source="owner")
    assert set(changed) == {to_payee, yono}
    cats = {r["id"]: r["category"] for r in conn.execute("SELECT id, category FROM transactions")}
    assert cats[to_payee] == "Home Construction"
    assert cats[yono] == "Home Construction"                   # 'Ac x9418' is the counterparty
    assert cats[own_side] == "Transfer"
    assert cats[hdfc_own] == "Transfer"
    assert payee_rules.matching_count(conn, "account", "418") == 2


def test_an_account_rule_never_matches_the_owners_registered_accounts(conn):
    own_account(conn, "HDFC", "8418")
    # a credit to the owner's own 8418 account worded with 'to': "credited to a/c ... 8418"
    credit = row(conn, "payer@okaxis",
                 "Rs.100.00 credited to a/c XXXXXXXX8418 on 01-10-26 by a/c linked to VPA payer@okaxis",
                 txn_type="credit")
    _rule, changed = payee_rules.save_rule(conn, "account", "418", "a/c ...418", "Home Construction",
                                           source="owner")
    assert changed == []
    assert payee_rules.matching_count(conn, "account", "418") == 0
