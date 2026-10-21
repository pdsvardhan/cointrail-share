"""iter-203 E10 — one payee, every rail and spelling.

The same builder is paid by UPI (alias spelling), by email-confirmed NEFT (a different spelling), by
YONO (the name cut to 6 characters) and by IMPS (only the account's last digits). Spelling variants of
a known payee alias now match it; a YONO-truncated name matches when — and only when — the bank's
wording truncates and the prefix maps to exactly one alias; an account alias is a payee rule (data in
the DB, never code). Payees and numbers are made up."""
from modules import payee_rules
from modules.categorizer import categorize
from scripts.iter203_be2_steps import step_recategorize_sent_to_people


def yono(name, acct="x9876", amount="3,00,000.00"):
    return (f"Your Ac x1234 debited Rs.{amount} for transfer to {name} Ac {acct} dt 14.03.26 "
            f"Ref SBI000000000001. If not done by you, call 1800111109. YONO SBI")


def imps(acct_tail):
    return (f"Dear Customer, Your a/c no. XXXXXXXX1234 is debited for Rs.3,00,000.00 on 11-03-2026 "
            f"and credited to a/c no. XXXXXXX{acct_tail} (IMPS Ref no 600000000001).")


def hdfc_sent(payee):
    return (f"Sent Rs.500.00 From HDFC Bank A/C *1111 To {payee} On 08/10/26 Ref 600000000017 "
            f"Not You? Call 18002586161/SMS BLOCK UPI to 7308080808")


def add_alias(conn, pattern, merchant, category):
    conn.execute("INSERT INTO merchant_aliases (pattern, match_type, merchant_name, category, source) "
                 "VALUES (?, 'contains', ?, ?, 'manual')", (pattern, merchant, category))
    conn.commit()


def test_spelling_variants_of_a_known_payee_match_it(conn):
    add_alias(conn, "sujata enterprises", "Sujata Enterprises", "Home Construction")
    assert categorize("Sujatha enterprises", context="bank")[:2] == ("Sujata Enterprises", "Home Construction")
    assert categorize("SUJAATA ENTERPRISES", context="upi")[1] == "Home Construction"


def test_a_variant_never_captures_a_different_payee(conn):
    add_alias(conn, "sujata enterprises", "Sujata Enterprises", "Home Construction")
    assert categorize("Sujit enterprises", context="bank")[1] != "Home Construction"
    assert categorize("Sujata Kumari", context="upi")[1] == "Transfer"


def test_a_yono_truncated_name_matches_its_one_alias(conn):
    add_alias(conn, "sujata enterprises", "Sujata Enterprises", "Home Construction")
    assert categorize("Sujath", context="bank", raw_text=yono("Sujath"))[:2] == (
        "Sujata Enterprises", "Home Construction")


def test_truncation_is_trusted_only_where_the_bank_truncates(conn):
    add_alias(conn, "sujata enterprises", "Sujata Enterprises", "Home Construction")
    # the same 6 letters in a wording that prints full names: a different person
    assert categorize("Sujath", context="upi", raw_text=hdfc_sent("Sujath"))[1] == "Transfer"
    # shorter than YONO's 6-character cut: the name is complete, so no prefix guess
    assert categorize("Sujat", context="bank", raw_text=yono("Sujat"))[1] == "Transfer"


def test_a_prefix_shared_by_two_payees_matches_neither(conn):
    add_alias(conn, "sujata enterprises", "Sujata Enterprises", "Home Construction")
    add_alias(conn, "sujata traders", "Sujata Traders", "Shopping")
    assert categorize("Sujath", context="bank", raw_text=yono("Sujath"))[1] == "Transfer"


def test_an_account_alias_files_imps_and_yono_payments_to_that_account(conn):
    payee_rules.save_rule(conn, "account", "876", "a/c ...876", "Home Construction",
                          merchant_name="Sujata Enterprises", source="owner")
    assert categorize("IMPS to a/c XXXXXXX876", context="bank", raw_text=imps("876"))[:2] == (
        "Sujata Enterprises", "Home Construction")
    # YONO prints the name, the account only in the message ("Ac x9876")
    assert categorize("Kumara", context="bank", raw_text=yono("Kumara", acct="x9876"))[1] == "Home Construction"
    assert categorize("IMPS to a/c XXXXXXX877", context="bank", raw_text=imps("877"))[1] == "Transfer"


def _old(conn, desc, raw_text, source="sms", amount=300000.0, is_duplicate=0):
    cur = conn.execute(
        "INSERT INTO transactions (amount, raw_description, merchant, category, date, source, card_last4, "
        "txn_type, confidence, raw_text, is_duplicate) VALUES (?, ?, ?, 'Transfer', '2026-03-14', ?, '', 'debit', "
        "'medium', ?, ?)",
        (amount, desc, desc, source, raw_text, is_duplicate),
    )
    conn.commit()
    return cur.lastrowid


def test_owner_account_rule_plus_the_data_step_move_every_builder_payment(conn):
    add_alias(conn, "sujata enterprises", "Sujata Enterprises", "Home Construction")
    via_imps = _old(conn, "IMPS to a/c XXXXXXX876", imps("876"))
    via_yono = _old(conn, "Sujath", yono("Sujath", acct="x9876"), amount=321000.0)
    email_copy = _old(conn, "Sujatha enterprises", "Transaction success | NEFT to Sujatha enterprises",
                      source="email", amount=321000.0, is_duplicate=1)
    email_only = _old(conn, "Sujatha enterprises", "Transaction success | NEFT to Sujatha enterprises",
                      source="email", amount=234000.0)
    person = _old(conn, "RAMESH KUMAR", hdfc_sent("RAMESH KUMAR"), amount=2000.0)

    # the owner's private data step: the account is the builder's
    _rule, by_rule = payee_rules.save_rule(conn, "account", "876", "a/c ...876", "Home Construction",
                                           merchant_name="Sujata Enterprises", source="owner")
    assert set(by_rule) == {via_imps, via_yono}

    res = step_recategorize_sent_to_people(conn, dry_run=False)
    assert {r["id"] for r in res["rows"]} == {email_copy, email_only}
    cats = {r["id"]: r["category"] for r in conn.execute("SELECT id, category FROM transactions").fetchall()}
    assert all(cats[i] == "Home Construction" for i in (via_imps, via_yono, email_copy, email_only))
    assert cats[person] == "Transfer"
