"""Fixes found by the iter-18 staging rehearsal on a copy of the live DB (2026-10-20):
own-account transfers, loan repayments, one transfer reported by two SBI sender families, and
old SMS rows stored by the retired generic patterns."""
from modules.categorizer import categorize
from modules.txn_store import insert_transaction
from modules.sms.processing import refresh_existing_sms_rows


def _accounts(make_card):
    make_card("HDFC", "6611", name="Account", active=0)
    make_card("HSBC", "4706", name="Account", active=0)
    make_card("SBI", "4321", name="Savings account", active=0)
    make_card("SBI", "2111", name="Cashback", statement_day=19, due_day=8)


# --- own accounts -------------------------------------------------------------------------------

def test_transfer_to_an_own_account_is_a_self_transfer(conn, make_card):
    _accounts(make_card)
    assert categorize("NEFT via Online to SBOI A/c ***4321 Owner SBI", "bank")[1] == "Self Transfer"
    assert categorize("IMPS to a/c XXXXXXX706", "bank")[1] == "Self Transfer"            # HSBC ...4706
    assert categorize("IMPS to a/c XXXXXXX611", "bank")[1] == "Self Transfer"            # HDFC ...6611
    assert categorize("transfer towards account 111-222***-706", "bank")[1] == "Self Transfer"


def test_transfer_to_someone_elses_account_is_not(conn, make_card):
    _accounts(make_card)
    assert categorize("transfer to Anita Ac Xx7777", "bank")[1] != "Self Transfer"
    assert categorize("APARNA CINEM", "upi")[1] != "Self Transfer"


def test_paying_an_own_active_card_from_an_account_is_a_bill_payment(conn, make_card):
    _accounts(make_card)
    assert categorize("NEFT to card a/c XXXXXXXXXXXX2111", "bank")[1] == "Finance"


def test_card_spends_are_never_reclassified_by_account_rules(conn, make_card):
    _accounts(make_card)
    assert categorize("REPAIR AC 4321 SERVICES", "card")[1] != "Self Transfer"


def test_loan_standing_instruction_is_a_loan_repayment(conn, make_card):
    _accounts(make_card)
    assert categorize("Loan repayment (EMI) - Standing instruction to Loan A/c No.XXXXX300011", "bank")[1] == \
        "Loan Repayment"


# --- twin alerts ----------------------------------------------------------------------------------

def _sms(conn, sid, sender, body, ts="2026-08-12T06:00:00"):
    conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, is_financial, is_processed) "
                 "VALUES (?, ?, ?, ?, 1, 1)", (sid, sender, body, ts))


def test_one_transfer_reported_by_yono_and_core_banking_is_one_row(conn, make_card):
    _accounts(make_card)
    _sms(conn, 1, "AD-SBYONO-S", "Your Ac Xx4321 debited Rs.3,00,000.00 for transfer to Anita Ac Xx7777 dt 12.08.26 Ref 200000017.")
    # core banking reports it 8 minutes after YONO: past repeat-alert's 5 minutes, inside twin-alert's 10
    # (the real pairs are 19-127 s apart; 30 minutes apart is another transfer, see test_merge F3)
    _sms(conn, 2, "AD-CBSSBI-S", "Your A/C XXXXX004321 Debited INR 3,00,000.00 on 12/08/26 -Transferred to Mrs. ANITA RAO.",
         ts="2026-08-12T06:08:00")
    insert_transaction(conn, amount=300000, date="2026-08-12", source="sms", source_ref=1, raw_description="Anita",
                       card_last4="4321", ref_no="200000017", context="bank")
    insert_transaction(conn, amount=300000, date="2026-08-12", source="sms", source_ref=2,
                       raw_description="Mrs. ANITA RAO", card_last4="4321", context="bank")
    live = conn.execute("SELECT COUNT(*) c FROM transactions WHERE is_duplicate = 0").fetchone()["c"]
    rule = conn.execute("SELECT merge_rule FROM transactions WHERE is_duplicate = 1").fetchone()
    assert live == 1 and rule["merge_rule"] == "twin-alert"


def test_two_real_transfers_to_different_people_from_two_channels_stay_two(conn, make_card):
    _accounts(make_card)
    _sms(conn, 1, "AD-SBYONO-S", "Your Ac Xx4321 debited Rs.5,000.00 for transfer to Ramesh Ac Xx1111 dt 12.08.26 Ref 1.")
    _sms(conn, 2, "AD-CBSSBI-S", "Your A/C XXXXX004321 Debited INR 5,000.00 on 12/08/26 -Transferred to Mr. SURESH BABU.")
    insert_transaction(conn, amount=5000, date="2026-08-12", source="sms", source_ref=1, raw_description="Ramesh",
                       card_last4="4321", ref_no="1", context="bank")
    insert_transaction(conn, amount=5000, date="2026-08-12", source="sms", source_ref=2,
                       raw_description="Mr. SURESH BABU", card_last4="4321", context="bank")
    assert conn.execute("SELECT COUNT(*) c FROM transactions WHERE is_duplicate = 0").fetchone()["c"] == 2


# --- old rows from retired generic patterns -------------------------------------------------------

def test_refresh_corrects_an_unknown_debit_from_an_sbi_emandate(conn, make_card):
    _accounts(make_card)
    body = ("Transaction Successful! Rs.199.00 has been debited to your SBI Credit Card XX2111 against recurring "
            "e-mandate registered by you at merchant site. Details: Merchant: NETFLIX SiHub ID: Xqzzzzz004 "
            "Trxn. Ref. No.: 600000000000004 Date: 03-Oct-2026. Time: 16:07:43")
    conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, is_financial, is_processed) "
                 "VALUES (10, 'AX-SBICRD-S', ?, '2026-10-02T21:00:00', 1, 1)", (body,))
    conn.execute("INSERT INTO transactions (amount, raw_description, merchant, category, date, source, source_ref, "
                 "bank, card_last4, txn_type, confidence, raw_text) VALUES (199, 'Unknown debit', 'Unknown Debit', "
                 "'Transfer', '2026-10-02', 'sms', '10', 'Unknown', '2111', 'debit', 'medium', ?)", (body,))
    conn.commit()
    dry = refresh_existing_sms_rows(conn, dry_run=True)
    assert dry["corrected"] == 1
    assert conn.execute("SELECT raw_description FROM transactions").fetchone()["raw_description"] == "Unknown debit"
    refresh_existing_sms_rows(conn, dry_run=False)
    r = conn.execute("SELECT date, merchant, category, ref_no, kind FROM transactions").fetchone()
    assert r["date"] == "2026-10-03" and r["category"] == "Subscriptions" and r["ref_no"] == "600000000000004"
    assert r["kind"] == "purchase"


def test_refresh_fixes_a_foreign_amount_stored_as_rupees(conn, make_card):
    _accounts(make_card)
    body = ("Transaction Successful! USD118.00 has been debited to your SBI Credit Card XX2111 against recurring "
            "e-mandate registered by you at merchant site. Details: Merchant: ANTHROPICCLAUDESUB SiHub ID: X "
            "Trxn. Ref. No.: 600000000000006 Date: 25-Sep-2026. Time: 10:00:00")
    conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, is_financial, is_processed) "
                 "VALUES (11, 'AD-SBICRD-S', ?, '2026-09-25T05:00:00', 1, 1)", (body,))
    conn.execute("INSERT INTO transactions (amount, raw_description, merchant, category, date, source, source_ref, "
                 "bank, card_last4, txn_type, confidence, raw_text) VALUES (118, 'ANTHROPICCLAUDESUB', 'Anthropic', "
                 "'Subscriptions', '2026-09-25', 'sms', '11', 'SBI', '2111', 'debit', 'high', ?)", (body,))
    conn.commit()
    refresh_existing_sms_rows(conn, dry_run=False)
    r = conn.execute("SELECT amount, currency, original_amount, fx_estimated FROM transactions").fetchone()
    assert r["currency"] == "USD" and r["original_amount"] == 118 and r["fx_estimated"] == 1
    assert r["amount"] > 5000            # an INR estimate, not Rs 118


def test_refresh_retires_a_row_minted_from_a_notice(conn, make_card):
    _accounts(make_card)
    body = "Your payment for Swiggy order #600000000000003 was not completed. Any amount if debited from your card will get refunded within 4-7 days."
    conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, is_financial, is_processed) "
                 "VALUES (12, 'AD-SWIGGY-S', ?, '2026-09-13T05:00:00', 1, 1)", (body,))
    conn.execute("INSERT INTO transactions (amount, raw_description, merchant, category, date, source, source_ref, "
                 "bank, card_last4, txn_type, confidence, raw_text) VALUES (450, 'Unknown debit', 'Unknown Debit', "
                 "'Transfer', '2026-09-13', 'sms', '12', 'Unknown', '', 'debit', 'low', ?)", (body,))
    conn.commit()
    res = refresh_existing_sms_rows(conn, dry_run=False)
    r = conn.execute("SELECT is_duplicate, merge_rule FROM transactions").fetchone()
    assert r["is_duplicate"] == 1 and r["merge_rule"].startswith("reclassified-")
    assert sum(v for k, v in res.items() if k.startswith("retired_")) == 1


# --- analytics: loans are an outflow, not spend ---------------------------------------------------

def test_loan_repayment_is_reported_as_an_outflow(conn, make_card):
    from routes.analytics import analytics_overview
    _accounts(make_card)
    insert_transaction(conn, amount=75000, date="2026-08-20", source="sms", source_ref=20,
                       raw_description="Loan repayment (EMI) - Standing instruction to Loan A/c No.XXXXX300011",
                       card_last4="4321", context="bank")
    insert_transaction(conn, amount=500, date="2026-08-20", source="sms", source_ref=21,
                       raw_description="Blinkit", card_last4="2111", context="cc")
    o = analytics_overview(period="2026-08")
    assert o["total"] == 500
    assert o["outflows"]["loans"] == {"total": 75000, "count": 1}


# --- add-on cards: one bill per billing group ----------------------------------------------------

def test_addon_lines_roll_into_the_primary_statement(conn, make_card):
    import services.cards_service as cs
    make_card("ICICI", "1019", name="Sapphiro", statement_day=21, due_day=10)
    make_card("ICICI", "8013", name="Sapphiro Amex", statement_day=21, due_day=10)
    conn.execute("UPDATE cards SET billing_group = 'sapphiro' WHERE last4 IN ('1019', '8013')")
    conn.commit()
    insert_transaction(conn, amount=6805.69, date="2026-03-12", source="card", source_ref="s#1",
                       raw_description="PROTON AG", card_last4="8013", context="card")
    insert_transaction(conn, amount=500, date="2026-03-15", source="card", source_ref="s#2",
                       raw_description="BIGTREE", card_last4="1019", context="card")
    cs.backfill_statements_logic(conn)
    rows = conn.execute("SELECT c.last4, s.total_amount FROM statements s JOIN cards c ON c.id = s.card_id").fetchall()
    assert [(r["last4"], r["total_amount"]) for r in rows] == [("1019", 7305.69)]


def test_addon_unbilled_runs_from_the_primary_close(conn, make_card):
    import services.cards_service as cs
    make_card("ICICI", "1019", name="Sapphiro", statement_day=21, due_day=10)
    make_card("ICICI", "8013", name="Sapphiro Amex", statement_day=21, due_day=10)
    conn.execute("UPDATE cards SET billing_group = 'sapphiro' WHERE last4 IN ('1019', '8013')")
    conn.commit()
    insert_transaction(conn, amount=6805.69, date="2026-03-12", source="card", source_ref="s#1",
                       raw_description="PROTON AG", card_last4="8013", context="card")
    insert_transaction(conn, amount=120, date="2026-10-05", source="sms", source_ref="9",
                       raw_description="PROTON", card_last4="8013", context="cc")
    cs.backfill_statements_logic(conn)
    from datetime import date
    cards = {c["last4"]: c for c in cs.build_cards_summary(conn, today=date(2026, 10, 19))["cards"]}
    assert cards["8013"]["addon_of"] == "1019"
    assert cards["8013"]["unbilled"]["total"] == 120          # not the March statement line


def test_spend_on_the_close_day_that_missed_the_statement_is_unbilled(conn, make_card):
    """ICICI 0026: statement lines end 09-19; the 09-20 SMS and email spends are on the next bill."""
    import services.cards_service as cs
    from datetime import date
    make_card("ICICI", "0026", name="Amazon Pay", statement_day=21, due_day=10)
    insert_transaction(conn, amount=1000, date="2026-09-10", source="card", source_ref="s#1",
                       raw_description="AMAZON", card_last4="0026", context="card")
    insert_transaction(conn, amount=1000, date="2026-09-10", source="sms", source_ref="1",
                       raw_description="Amazon", card_last4="0026", context="cc")          # on the statement
    insert_transaction(conn, amount=917.78, date="2026-09-20", source="sms", source_ref="2",
                       raw_description="K V KRISHNA RAO", card_last4="0026", context="cc")  # missed it
    insert_transaction(conn, amount=433, date="2026-09-21", source="email", source_ref="3",
                       raw_description="AMAZON PAY", card_last4="0026", context="email-card")
    insert_transaction(conn, amount=200, date="2026-10-02", source="sms", source_ref="4",
                       raw_description="Zomato", card_last4="0026", context="cc")
    cs.backfill_statements_logic(conn)
    card = [c for c in cs.build_cards_summary(conn, today=date(2026, 10, 19))["cards"] if c["last4"] == "0026"][0]
    assert card["latest_statement"]["billing_end"] == "2026-09-21"
    assert card["unbilled"]["total"] == 1550.78                 # 917.78 + 433 + 200, not the billed 1000


# --- recategorize never undoes a payment label or a label inherited at merge time --------------

def test_recategorize_keeps_bill_payments_and_inherited_labels(conn, make_card):
    from modules.categorizer import recategorize_with_context
    _accounts(make_card)
    # a statement payment line already relabelled by the PDF fix
    conn.execute("INSERT INTO transactions (amount, raw_description, merchant, category, date, source, source_ref, "
                 "card_last4, txn_type, confidence, kind) VALUES (15336, '+', 'Card bill payment', 'Finance', "
                 "'2026-06-20', 'card', 'p#1', '4956', 'credit', 'high', 'bill_payment')")
    # a statement line that took 'Self Transfer' from its linked SMS copy
    conn.execute("INSERT INTO transactions (id, amount, raw_description, merchant, category, date, source, source_ref, "
                 "card_last4, txn_type, confidence, kind) VALUES (500, 90000, 'NEFT CR 12345', 'Self Transfer', "
                 "'Self Transfer', '2026-08-12', 'card', 'p#2', '2111', 'debit', 'high', 'debit')")
    conn.execute("INSERT INTO transactions (amount, raw_description, merchant, category, date, source, source_ref, "
                 "card_last4, txn_type, confidence, is_duplicate, matched_txn_id) VALUES (90000, 'Self transfer', "
                 "'Self Transfer', 'Self Transfer', '2026-08-12', 'sms', '9', '2111', 'debit', 'high', 1, 500)")
    conn.commit()
    res = recategorize_with_context(conn, dry_run=False)
    assert res["skipped"].get("bill_payment") == 1 and res["skipped"].get("inherited_from_copy") == 1
    cats = {r["source_ref"]: r["category"] for r in conn.execute("SELECT source_ref, category FROM transactions")}
    assert cats["p#1"] == "Finance" and cats["p#2"] == "Self Transfer"


# --- verifier reverify2: account movements that name no merchant; meal-card context -------------

def test_account_movements_without_a_merchant_are_transfers_not_spend(conn, make_card):
    _accounts(make_card)
    assert categorize("DEBIT", "bank")[1] == "Transfer"                       # HSBC standing debit
    assert categorize("per your request", "bank")[1] == "Transfer"
    assert categorize("SBI internet banking transfer", "bank")[1] == "Transfer"  # not Bills & Utilities
    assert categorize("to a/c **9999", "upi")[1] == "Transfer"
    assert categorize("to a/c **611", "upi")[1] == "Self Transfer"            # an own account still wins
    assert categorize("Internet broadband bill", "bank")[1] == "Bills & Utilities"
    assert categorize("DEBIT", "card")[1] != "Transfer"                       # never on a card


def test_meal_card_rows_keep_their_context_on_recategorise(conn, make_card):
    """Pluxee isn't a card in the cards table; recategorise used to treat its spends as account
    movements and re-file COMPASS / VIJETHA SUP as person-name Transfers."""
    from modules.categorizer import context_for_row, recategorize_with_context
    body = ("Rs. 141.00 spent from Pluxee  Meal Card wallet, card no.xx9824 on 13-05-2025 13:20:34 "
            "at COMPASS . Avl bal Rs.169.46. Not you call 44394415653")
    assert context_for_row("sms", "9824", body, set()) == "meal_card"
    t = insert_transaction(conn, amount=141.0, date="2025-05-13", source="sms", source_ref="px1",
                           raw_description="COMPASS", card_last4="9824", raw_text=body, context="meal_card")
    before = conn.execute("SELECT category FROM transactions WHERE id=?", (t["id"],)).fetchone()["category"]
    assert before != "Transfer"
    res = recategorize_with_context(conn, dry_run=False)
    after = conn.execute("SELECT category FROM transactions WHERE id=?", (t["id"],)).fetchone()["category"]
    assert after == before, res


def test_atm_alerts_are_cash_withdrawals_whatever_the_location_says(conn):
    assert categorize("+MAIN ROAD COLONY", "atm")[1] == "ATM Withdrawal"
    assert categorize("+2ND CD AT APARTMENT RD", "atm")[1] == "ATM Withdrawal"


def test_mart_inside_a_surname_is_not_a_shop(conn):
    assert categorize("SAMARTHI RAVI TEJA", "upi")[1] != "Shopping"
    assert categorize("MARTINA ROSE", "upi")[1] != "Shopping"
    assert categorize("XYZ SUPER MART", "upi")[1] == "Shopping"
