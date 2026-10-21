"""Card paid / overdue corner cases — iteration 203 E8 (independent review rows 26, 35, 36, 37).

All card numbers, amounts, references and limits here are made up."""
from datetime import date

import services.cards_service as cs
from modules.pdf import store
from modules.pdf.parsers.base import Statement, Transaction
from modules.txn_store import insert_transaction


def add_statement(conn, last4, billing_start, billing_end, stated=None, computed=None, due_date=None):
    card = conn.execute("SELECT id FROM cards WHERE last4=?", (last4,)).fetchone()["id"]
    conn.execute(
        "INSERT INTO statements (card_id, month, year, total_amount, stated_total, computed_total, billing_start, "
        "billing_end, due_date, due_date_source, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'parsed')",
        (card, int(billing_end[5:7]), int(billing_end[:4]), stated if stated is not None else computed,
         stated, computed, billing_start, billing_end, due_date, "bank" if due_date else None))


def card_credit(conn, last4, on, amount, desc="SWIGGY INSTAMART REFUND", source="card"):
    return insert_transaction(conn, amount=amount, date=on, source=source, source_ref=f"{source}-{on}-{amount}",
                              raw_description=desc, card_last4=last4, txn_type="credit", context="card",
                              link=False)["id"]


def summary(conn, today):
    return {c["last4"]: c for c in cs.build_cards_summary(conn, today=today)["cards"]}


def add_sms(conn, body, ts, sender="VM-BANKCC-S"):
    return conn.execute("INSERT INTO sms (sender, body, phone_timestamp) VALUES (?, ?, ?)",
                        (sender, body, ts)).lastrowid


def payments(conn, last4):
    return [dict(r) for r in conn.execute(
        "SELECT amount, payment_date, source, sms_id, email_id, ref, stmt_ref FROM cc_payments WHERE card_last4=? "
        "ORDER BY payment_date, id", (last4,))]


# --- AC1: a credit after the statement closes reduces what is still owed ----------------------------------

def test_refund_after_the_statement_counts_toward_the_bill(conn, make_card):
    """Jul bill Rs 21,177; a Rs 613 refund posts the day after the close; the owner pays the rest,
    Rs 20,564. It is paid — not 'overdue Rs 613' for ten days."""
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    add_statement(conn, "4400", "2026-06-23", "2026-07-22", stated=21177.0, due_date="2026-08-12")
    card_credit(conn, "4400", "2026-07-23", 613.0)
    cs.record_card_payment(conn, "4400", 20564.0, "2026-08-03", "sms")
    c = summary(conn, date(2026, 8, 14))["4400"]
    assert (c["payment_status"], c["remaining_due"]) == ("paid", 0.0)
    assert c["credits_since_close"] == 613.0


def test_a_refund_dated_on_the_close_day_but_billed_next_month_counts_toward_the_bill(conn, make_card):
    """Verifier round 2: the statement line of the Rs 613 refund is dated ON the Jul close day (the bank
    billed it on the Aug statement); its SMS copy arrived the next day. No cashback hides it here."""
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    add_statement(conn, "4400", "2026-06-23", "2026-07-22", stated=21177.0, due_date="2026-08-12")
    line = card_credit(conn, "4400", "2026-07-22", 613.0)
    sms_copy = card_credit(conn, "4400", "2026-07-23", 613.0, desc="SWIGGY INSTAMART PR BENGALURU", source="sms")
    conn.execute("UPDATE transactions SET is_duplicate = 1, matched_txn_id = ? WHERE id = ?", (line, sms_copy))
    conn.commit()
    cs.record_card_payment(conn, "4400", 20564.0, "2026-08-03", "sms")
    c = summary(conn, date(2026, 8, 14))["4400"]
    assert (c["payment_status"], c["remaining_due"]) == ("paid", 0.0)
    assert c["credits_since_close"] == 613.0


def test_a_close_day_credit_with_nothing_after_the_close_stays_on_its_own_statement(conn, make_card):
    """No copy shows it posted after the close: the bank's total for that statement already has it."""
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    add_statement(conn, "4400", "2026-06-23", "2026-07-22", stated=21177.0, due_date="2026-08-12")
    card_credit(conn, "4400", "2026-07-22", 613.0)
    cs.record_card_payment(conn, "4400", 20564.0, "2026-08-03", "sms")
    c = summary(conn, date(2026, 8, 14))["4400"]
    assert (c["payment_status"], c["remaining_due"]) == ("overdue", 613.0)
    assert c["credits_since_close"] == 0.0


def test_a_credit_after_the_due_date_does_not_rescue_the_bill(conn, make_card):
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    add_statement(conn, "4400", "2026-06-23", "2026-07-22", stated=21177.0, due_date="2026-08-12")
    card_credit(conn, "4400", "2026-08-13", 613.0)
    cs.record_card_payment(conn, "4400", 20564.0, "2026-08-03", "sms")
    c = summary(conn, date(2026, 8, 14))["4400"]
    assert (c["payment_status"], c["remaining_due"]) == ("overdue", 613.0)


def test_payment_status_counts_credits_but_a_credit_alone_is_not_a_payment():
    assert cs.payment_status(1000.0, 600.0, "2026-08-12", "2026-07-22", date(2026, 8, 1), credits=400.0) == ("paid", 0.0)
    assert cs.payment_status(1000.0, 0.0, "2026-08-12", "2026-07-22", date(2026, 8, 1), credits=400.0) == ("unpaid", 600.0)


# --- AC2: payments settle the oldest unpaid bill first --------------------------------------------------------

def test_late_payment_of_last_months_bill_does_not_mark_the_new_bill_paid(conn, make_card):
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    add_statement(conn, "4400", "2026-07-23", "2026-08-22", computed=10000.0, due_date="2026-09-12")
    add_statement(conn, "4400", "2026-08-23", "2026-09-22", computed=5000.0, due_date="2026-10-12")
    cs.record_card_payment(conn, "4400", 10000.0, "2026-09-25", "sms")          # last month's bill, paid late
    c = summary(conn, date(2026, 9, 30))["4400"]
    assert (c["payment_status"], c["remaining_due"]) == ("unpaid", 5000.0)
    assert c["previous_bill"]["remaining"] == 0.0 and c["previous_bill"]["paid_after_close"] == 10000.0


def test_a_bank_balance_already_includes_the_carried_bill(conn, make_card):
    """The bank's Total Amount Due is the whole balance: the late payment is a part-payment of it."""
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    add_statement(conn, "4400", "2026-07-23", "2026-08-22", stated=10000.0, due_date="2026-09-12")
    add_statement(conn, "4400", "2026-08-23", "2026-09-22", stated=15350.0, due_date="2026-10-12")
    cs.record_card_payment(conn, "4400", 10000.0, "2026-09-25", "sms")
    c = summary(conn, date(2026, 9, 30))["4400"]
    assert (c["payment_status"], c["remaining_due"]) == ("partial", 5350.0)
    assert c.get("previous_bill") is None


def test_previous_bill_paid_on_time_leaves_new_payments_to_the_new_bill(conn, make_card):
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    add_statement(conn, "4400", "2026-07-23", "2026-08-22", computed=10000.0, due_date="2026-09-12")
    add_statement(conn, "4400", "2026-08-23", "2026-09-22", computed=5000.0, due_date="2026-10-12")
    cs.record_card_payment(conn, "4400", 10000.0, "2026-09-05", "sms")
    cs.record_card_payment(conn, "4400", 5000.0, "2026-10-05", "sms")
    c = summary(conn, date(2026, 10, 6))["4400"]
    assert (c["payment_status"], c["remaining_due"]) == ("paid", 0.0)


# --- AC3: statement payment lines are payments ------------------------------------------------------------------

def _stmt(lines, payments_):
    st = Statement(bank="sbi", card_last4="4411", period_start=date(2026, 3, 20), period_end=date(2026, 4, 19))
    st.transactions = lines
    st.payments = payments_
    return st


def test_statement_payment_line_with_no_other_evidence_pays_the_bill(conn, make_card):
    make_card("SBI", "4411", statement_day=19, due_day=8)
    st = _stmt([Transaction("2026-04-02", "AMAZON", 900.0, line_index=1)],
               [Transaction("2026-04-18", "PAYMENT RECEIVED 000DP0000001", 15000.0, is_credit=True, line_index=2)])
    res = store.store_statement(conn, "/data/statements/S.pdf", "sbi", st)
    assert res["payments_recorded"] == 1
    p = payments(conn, "4411")
    assert [(x["amount"], x["payment_date"], x["source"], x["stmt_ref"]) for x in p] == \
        [(15000.0, "2026-04-18", "statement", "S.pdf#2")]
    add_statement(conn, "4411", "2026-02-20", "2026-03-19", stated=15000.0, due_date="2026-04-08")
    c = summary(conn, date(2026, 4, 25))["4411"]
    assert c["payment_status"] == "paid"
    again = store.store_statement(conn, "/data/statements/S.pdf", "sbi", st)
    copy = store.store_statement(conn, "/data/statements/S_1.pdf", "sbi", st)       # re-downloaded copy
    assert again["payments_recorded"] == copy["payments_recorded"] == 0 and len(payments(conn, "4411")) == 1


def test_statement_payment_line_is_the_same_payment_as_the_sms(conn, make_card):
    make_card("SBI", "4411", statement_day=19, due_day=8)
    cs.record_card_payment(conn, "4411", 15000.0, "2026-04-20", "sms", sms_id=77)
    st = _stmt([], [Transaction("2026-04-17", "PAYMENT RECEIVED 000DP0000001", 15000.0, is_credit=True, line_index=4)])
    store.store_statement(conn, "/data/statements/S.pdf", "sbi", st)
    p = payments(conn, "4411")
    assert len(p) == 1 and (p[0]["sms_id"], p[0]["stmt_ref"]) == (77, "S.pdf#4")


def test_two_payment_lines_of_one_statement_are_two_payments(conn, make_card, monkeypatch):
    """The bill was paid twice the same day (CRED and the autopay); the statement prints both."""
    make_card("SBI", "4411", statement_day=19, due_day=8)
    cs.record_card_payment(conn, "4411", 5236.99, "2026-04-08", "email-cred", email_id=9, ref="cred:ZZZ999")
    st = _stmt([], [Transaction("2026-04-08", "BBPS PMT BBPSDP0000000001", 5236.99, is_credit=True, line_index=3),
                    Transaction("2026-04-08", "BBPS PMT BBPSDP0000000002", 5236.99, is_credit=True, line_index=7)])
    monkeypatch.setattr(store, "statement_files", lambda directory=None: ["/data/statements/S.pdf"])
    monkeypatch.setattr(store, "parse_file", lambda path: ("sbi", st))
    assert store.payments_from_statements(conn, dry_run=True)["would_record"] == 1
    assert store.payments_from_statements(conn, dry_run=False)["recorded"] == 1
    assert [(p["amount"], p["stmt_ref"]) for p in payments(conn, "4411")] == [(5236.99, "S.pdf#3"), (5236.99, "S.pdf#7")]


def test_a_bounced_payment_on_the_statement_is_no_payment(conn, make_card):
    make_card("SBI", "4411", statement_day=19, due_day=8)
    st = _stmt([], [Transaction("2026-04-08", "DIRECT DEBIT PAYMENT(ACH)", 3454.84, is_credit=True, line_index=5),
                    Transaction("2026-04-08", "INSUFFICIENT FUNDS", 3454.84, is_credit=False, line_index=6)])
    res = store.store_statement(conn, "/data/statements/S.pdf", "sbi", st)
    assert res["payments_recorded"] == 0 and payments(conn, "4411") == []


def test_payments_from_statements_dry_run_writes_nothing(conn, make_card, monkeypatch):
    make_card("SBI", "4411", statement_day=19, due_day=8)
    st = _stmt([], [Transaction("2026-04-18", "PAYMENT RECEIVED 000DP0000001", 15000.0, is_credit=True, line_index=2)])
    monkeypatch.setattr(store, "statement_files", lambda directory=None: ["/data/statements/S.pdf"])
    monkeypatch.setattr(store, "parse_file", lambda path: ("sbi", st))
    res = store.payments_from_statements(conn, dry_run=True)
    assert res["would_record"] == 1 and payments(conn, "4411") == []
    res = store.payments_from_statements(conn, dry_run=False)
    assert res["recorded"] == 1 and len(payments(conn, "4411")) == 1


# --- AC4: genuine same-amount payments are kept apart; one message is one payment -------------------------------

_RECEIVED = ("DEAR CARDMEMBER, PAYMENT OF Rs. 5000.00 RECEIVED TOWARDS YOUR CREDIT CARD ENDING WITH 4400 ON {d}."
             "YOUR AVAILABLE LIMIT IS RS. {limit}")
_ONLINE = ("Bank Cardmember, Online Payment of Rs.5000 vide Ref# {ref} was credited to your card ending 4400 "
           "On {d2}_value Date {d2}")


def test_two_genuine_same_amount_payments_within_two_days_both_count(conn, make_card):
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    add_sms(conn, _RECEIVED.format(d="7-10-26", limit="100000.00"), "2026-10-07T06:35:08")
    add_sms(conn, _RECEIVED.format(d="8-10-26", limit="105000.00"), "2026-10-08T09:10:00")
    cs.backfill_payments_logic(conn)
    assert [p["amount"] for p in payments(conn, "4400")] == [5000.0, 5000.0]


def test_the_banks_two_messages_for_one_payment_are_one_payment(conn, make_card):
    """The bank confirms a payment twice, in two wordings, a day apart (one names a reference)."""
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    add_sms(conn, _RECEIVED.format(d="7-10-26", limit="100000.00"), "2026-10-07T06:35:08")
    add_sms(conn, _ONLINE.format(ref="AB12CD34", d2="07/OCT/26"), "2026-10-08T07:37:53")
    cs.backfill_payments_logic(conn)
    cs.backfill_payments_logic(conn)                                   # hourly re-read: still one
    assert len(payments(conn, "4400")) == 1


def test_one_message_delivered_twice_is_one_payment(conn, make_card):
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    body = _RECEIVED.format(d="7-10-26", limit="100000.00")
    add_sms(conn, body, "2026-10-07T06:35:08.278")
    add_sms(conn, body, "2026-10-07T06:35:08.901")                     # the same SMS imported again
    same_ref = _ONLINE.format(ref="AB12CD34", d2="07/OCT/26")
    add_sms(conn, same_ref, "2026-10-08T07:37:53")
    add_sms(conn, same_ref, "2026-10-08T16:28:02")                     # re-sent hours later, same reference
    cs.backfill_payments_logic(conn)
    assert len(payments(conn, "4400")) == 1


def test_two_payments_with_different_references_both_count(conn, make_card):
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    a = cs.record_card_payment(conn, "4400", 5000.0, "2026-10-07", "email-cred", email_id=1, ref="cred:AAA111")
    b = cs.record_card_payment(conn, "4400", 5000.0, "2026-10-08", "email-cred", email_id=2, ref="cred:BBB222")
    c = cs.record_card_payment(conn, "4400", 5000.0, "2026-10-08", "sms", sms_id=None)   # bank SMS for one of them
    assert (a["status"], b["status"], c["status"]) == ("inserted", "inserted", "exists")
    assert len(payments(conn, "4400")) == 2


def _legacy_payment(conn, sms_id, on, ref=None):
    """A payment row as an older build recorded it (no identity check, one row per message)."""
    return conn.execute(
        "INSERT INTO cc_payments (card_last4, amount, payment_date, source, sms_id, ref) VALUES ('4400', 5000.0, ?, "
        "'sms', ?, ?)", (on, sms_id, ref)).lastrowid


def test_old_double_records_of_one_payment_are_merged_by_the_identity_rules(conn, make_card):
    """Verifier round 2: three July-2025 payments sat in cc_payments twice — the bank's two wordings of
    one payment, recorded two days apart by an older build. The repair uses today's identity rules over
    the same +/-2 days record_card_payment uses, keeps the first row with every piece of evidence, and
    leaves genuine second payments alone."""
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    first = _legacy_payment(conn, add_sms(conn, _RECEIVED.format(d="2-7-25", limit="81410.63"),
                                          "2025-07-02T17:03:45"), "2025-07-02")
    second_wording = _legacy_payment(conn, add_sms(conn, _ONLINE.format(ref="AB12CD34", d2="03/JUL/2025"),
                                                   "2025-07-04T09:04:37"), "2025-07-04", ref="sms:AB12CD34")
    genuine_a = _legacy_payment(conn, add_sms(conn, _RECEIVED.format(d="7-10-26", limit="100000.00"),
                                              "2026-10-07T06:35:08"), "2026-10-07")
    genuine_b = _legacy_payment(conn, add_sms(conn, _RECEIVED.format(d="8-10-26", limit="105000.00"),
                                              "2026-10-08T09:10:00"), "2026-10-08")
    conn.commit()

    dry = cs.dedup_payments_logic(conn, dry_run=True)
    assert dry["dry_run"] is True and dry["deleted"] == 1 and [m["removed"] for m in dry["merged"]] == [second_wording]
    assert len(payments(conn, "4400")) == 4                                    # the dry run wrote nothing

    res = cs.dedup_payments_logic(conn, dry_run=False)
    assert res["deleted"] == 1
    ids = [r["id"] for r in conn.execute("SELECT id FROM cc_payments ORDER BY id")]
    assert ids == [first, genuine_a, genuine_b]
    kept = conn.execute("SELECT ref FROM cc_payments WHERE id = ?", (first,)).fetchone()
    assert kept["ref"] == "sms:AB12CD34"                                       # evidence carried over
    assert cs.dedup_payments_logic(conn, dry_run=True)["deleted"] == 0         # converges
