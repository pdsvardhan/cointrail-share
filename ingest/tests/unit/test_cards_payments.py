"""iter-18 M1 + contract 2: card-bill payments from bank SMS, CRED / ICICI emails and CRED debits on the
owner's bank accounts — all through record_card_payment."""
import services.cards_service as cs
from modules.txn_store import insert_transaction

_n = [0]


def add_sms(conn, body, ts, sender="AD-BANK-S"):
    return conn.execute("INSERT INTO sms (sender, body, phone_timestamp) VALUES (?, ?, ?)",
                        (sender, body, ts)).lastrowid


def add_email(conn, sender, subject, body, date_header):
    _n[0] += 1
    return conn.execute(
        "INSERT INTO emails (gmail_id, sender, sender_email, subject, body_snippet, date) VALUES (?, ?, ?, ?, ?, ?)",
        (f"pay-{_n[0]}", sender, sender, subject, body, date_header)).lastrowid


def add_statement(conn, last4, billing_start, billing_end, stated=None, computed=None, month=None, year=None):
    card = conn.execute("SELECT id FROM cards WHERE last4=?", (last4,)).fetchone()["id"]
    conn.execute(
        "INSERT INTO statements (card_id, month, year, total_amount, stated_total, computed_total, billing_start, billing_end, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'parsed')",
        (card, month or int(billing_end[5:7]), year or int(billing_end[:4]), stated if stated is not None else computed,
         stated, computed, billing_start, billing_end))


def cred_debit(conn, amount, on, account="6611", bank="HDFC", ref=None):
    return insert_transaction(conn, amount=amount, date=on, source="sms", source_ref=ref or f"debit-{on}-{amount}",
                              raw_description="CRED", bank=bank, card_last4=account, txn_type="debit",
                              merchant="CRED", category="Finance", context="upi", link=False)["id"]


def payments(conn, last4=None):
    q = "SELECT card_last4, amount, payment_date, source, sms_id, email_id, ref FROM cc_payments"
    rows = conn.execute(q + (" WHERE card_last4=?" if last4 else "") + " ORDER BY payment_date, id",
                        (last4,) if last4 else ()).fetchall()
    return [dict(r) for r in rows]


# --- contract 2 ----------------------------------------------------------------------------------------

def test_record_card_payment_is_idempotent_within_two_days_and_fills_evidence(conn, make_card):
    make_card("ICICI", "0026", statement_day=21, due_day=10)
    first = cs.record_card_payment(conn, "0026", 16469.81, "2026-10-07", "sms", sms_id=5559)
    again = cs.record_card_payment(conn, "0026", "16,469.81".replace(",", ""), "2026-10-09", "email-cred",
                                   email_id=14209, ref="cred:Gqzzzzzzz005")
    assert first["status"] == "inserted" and again == {"status": "exists", "id": first["id"], "card_last4": "0026"}
    later = cs.record_card_payment(conn, "0026", 16469.81, "2026-10-10", "email-cred")
    assert later["status"] == "inserted"                  # 3 days apart = a second payment
    p = payments(conn, "0026")[0]
    assert (p["source"], p["sms_id"], p["email_id"], p["ref"]) == ("sms", 5559, 14209, "cred:Gqzzzzzzz005")
    assert cs.record_card_payment(conn, "1234", 10, "2026-10-07", "sms")["status"] == "unknown_card"
    assert cs.record_card_payment(conn, "0026", 0, "2026-10-07", "sms")["status"] == "invalid"


def test_addon_payment_is_filed_on_the_primary_and_dedups_across_the_group(conn, make_card):
    make_card("ICICI", "1019", statement_day=21, due_day=10)
    make_card("ICICI", "8013", statement_day=21, due_day=10)
    conn.execute("UPDATE cards SET billing_group='sapphiro_group' WHERE last4 IN ('1019', '8013')")
    conn.execute("INSERT INTO cc_payments (card_last4, amount, payment_date, source) VALUES ('8013', 11811.47, '2025-12-13', 'sms')")
    assert cs.record_card_payment(conn, "1019", 11811.47, "2025-12-12", "email-bank")["status"] == "exists"
    res = cs.record_card_payment(conn, "8013", 5482.67, "2025-06-13", "sms")
    assert res["status"] == "inserted" and res["card_last4"] == "1019"


# --- SMS backfill: IST payment day ---------------------------------------------------------------------

def test_sms_payment_without_numeric_date_uses_the_ist_day(conn, make_card):
    make_card("ICICI", "1019", statement_day=21, due_day=10)
    # 19:00 UTC on 22 Apr = 00:30 IST on 23 Apr (the bank's own date in the text is "23-APR-25")
    add_sms(conn, "Payment of Rs 17,000.00 has been received on your ICICI Bank Credit Card XX1019 through Bharat Bill "
                  "Payment System on 23-APR-25.", "2025-04-22T19:00:00")
    cs.backfill_payments_logic(conn)
    assert [(p["amount"], p["payment_date"], p["source"]) for p in payments(conn, "1019")] == [(17000.0, "2025-04-23", "sms")]


# --- M1: email confirmations -------------------------------------------------------------------------

CRED_PAID_2111 = ("Customer Name, here is your payment confirmation Your credit card payment was successful SBI •••• 2111 "
                  "payment details amount paid &#8377;28,975.00 payment date Sep 04, 2026 credited to card Sep 04, 2026 "
                  "transaction summary Order ID: Yqzzzzzz006")


def test_cred_and_icici_payment_emails_become_payments_once(conn, make_card):
    make_card("SBI", "2111", statement_day=19, due_day=8, pdf_card_last4="9181")
    make_card("ICICI", "0026", statement_day=21, due_day=10)
    add_sms(conn, "Payment of Rs 16,469.81 has been received on your ICICI Bank Credit Card XX0026 through Bharat Bill Payment "
                  "System on 07-OCT-26.", "2026-10-07T07:00:00")
    e1 = add_email(conn, "protect@cred.club", "your credit card bill payment was successful", CRED_PAID_2111,
                   "Fri, 04 Sep 2026 15:00:00 +0530")
    e2 = add_email(conn, "protect@cred.club", "your credit card bill payment was successful",
                   "Customer Name, here is your payment confirmation Your credit card payment was successful in 44 seconds "
                   "ICICI Bank •••• 0026 payment details amount paid ₹16,469.81 payment date Oct 07, 2026 credited to card "
                   "Oct 07, 2026 transaction summary Order ID: Gqzzzzzzz005", "Wed, 07 Oct 2026 12:03:05 +0530")
    add_email(conn, "credit_cards@icici.bank.in", "Payment received on your ICICI Bank Credit Card.",
              "&nbsp; Dear Customer, Oct 07,2026 &nbsp; Greetings from ICICI Bank! We have received payment of INR 16,469.81 on "
              "your ICICI Bank Credit Card account 4315 XXXX XXXX 0026 on 07-Oct-2026. Looking forward",
              "Wed, 07 Oct 2026 12:10:00 +0530")
    res = cs.backfill_payments_logic(conn)
    assert res["emails"]["payments_found"] == 3 and res["emails"]["inserted"] == 1
    p2111 = payments(conn, "2111")
    assert [(p["amount"], p["payment_date"], p["source"], p["email_id"], p["ref"]) for p in p2111] == \
        [(28975.0, "2026-09-04", "email-cred", e1, "cred:Yqzzzzzz006")]
    p0026 = payments(conn, "0026")
    assert len(p0026) == 1 and p0026[0]["source"] == "sms" and p0026[0]["email_id"] == e2
    cs.backfill_payments_logic(conn)
    assert len(payments(conn)) == 2                        # re-running changes nothing


# --- M1: CRED debits on the owner's bank accounts ------------------------------------------------------

def setup_sep_cards(conn, make_card):
    make_card("SBI", "2111", statement_day=19, due_day=8, pdf_card_last4="9181")
    make_card("ICICI", "0026", statement_day=21, due_day=10)
    make_card("HDFC", "9434", statement_day=22, due_day=12)
    make_card("HDFC", "6611", name="Account", active=0)
    make_card("HSBC", "4706", name="Account", active=0)
    add_statement(conn, "2111", "2026-07-20", "2026-08-19", stated=28975.0, computed=27044.89)
    add_statement(conn, "0026", "2026-07-22", "2026-08-21", stated=108766.63, computed=23884.76)
    add_statement(conn, "9434", "2026-07-23", "2026-08-22", computed=18551.0)
    cs.record_card_payment(conn, "0026", 50000, "2026-08-29", "sms")          # part-payment earlier in the cycle
    cs.record_card_payment(conn, "9434", 17945, "2026-08-24", "email-cred")


def test_one_debit_paying_two_bills_is_split_between_them(conn, make_card):
    setup_sep_cards(conn, make_card)
    debit = cred_debit(conn, 87741.63, "2026-09-04", account="4706", bank="HSBC")
    res = cs.attribute_bank_debits_logic(conn)
    assert res["attributed"] == 2 and res["ambiguous"] == 0
    got = {(p["card_last4"], p["amount"], p["payment_date"], p["source"], p["ref"])
           for p in payments(conn) if p["source"] == "bank-debit-attributed"}
    assert got == {("0026", 58766.63, "2026-09-04", "bank-debit-attributed", f"txn:{debit}"),
                   ("2111", 28975.0, "2026-09-04", "bank-debit-attributed", f"txn:{debit}")}
    again = cs.attribute_bank_debits_logic(conn)
    assert again["attributed"] == 0 and len(payments(conn)) == 4


def test_debit_matching_one_statement(conn, make_card):
    make_card("SBI", "2971", statement_day=19, due_day=8, pdf_card_last4="8002")
    make_card("HDFC", "6611", name="Account", active=0)
    add_statement(conn, "2971", "2026-04-20", "2026-05-19", stated=3455.0)
    debit = cred_debit(conn, 3455.0, "2026-06-08")
    assert cs.attribute_bank_debits_logic(conn)["attributed"] == 1
    assert [(p["amount"], p["payment_date"], p["ref"]) for p in payments(conn, "2971")] == [(3455.0, "2026-06-08", f"txn:{debit}")]


def test_ambiguous_or_card_side_debits_are_left_alone(conn, make_card):
    make_card("SBI", "2111", statement_day=19, due_day=8)
    make_card("SBI", "2971", statement_day=19, due_day=8)
    add_statement(conn, "2111", "2026-04-20", "2026-05-19", stated=3455.0)
    add_statement(conn, "2971", "2026-04-20", "2026-05-19", stated=3455.0)
    cred_debit(conn, 3455.0, "2026-06-08")                                  # two statements fit: ambiguous
    cred_debit(conn, 3455.0, "2026-06-09", account="2111", bank="SBI")      # spent ON a card, not a payment
    res = cs.attribute_bank_debits_logic(conn)
    assert (res["attributed"], res["ambiguous"], res["debits"]) == (0, 1, 1)
    assert payments(conn) == []


def test_debit_attribution_yields_to_a_later_direct_confirmation(conn, make_card):
    make_card("HSBC", "8938", statement_day=4, due_day=25)
    make_card("HSBC", "4706", name="Account", active=0)
    add_statement(conn, "8938", "2026-02-05", "2026-03-04", computed=21206.0)
    cred_debit(conn, 21206.0, "2026-03-12", account="4706", bank="HSBC")
    assert cs.attribute_bank_debits_logic(conn)["attributed"] == 1
    # the card is credited four days after the money left the account; the bank SMS says so
    cs.record_card_payment(conn, "8938", 21206.0, "2026-03-16", "sms")
    res = cs.attribute_bank_debits_logic(conn)
    assert res["superseded_removed"] == 1 and res["already_confirmed"] == 1 and res["attributed"] == 0
    assert [(p["amount"], p["payment_date"], p["source"]) for p in payments(conn, "8938")] == [(21206.0, "2026-03-16", "sms")]


def test_bbps_attribution_chains_bank_debits_and_uses_the_contract(conn, make_card):
    make_card("SBI", "2111", statement_day=19, due_day=8)
    make_card("SBI", "2971", statement_day=19, due_day=8)
    add_statement(conn, "2111", "2026-08-20", "2026-09-19", stated=46088.0)
    add_sms(conn, "We have received payment of Rs.46,088.00 via BBPS & the same has been credited to your SBI Credit Card. "
                  "Your available limit is Rs.300,201.91.", "2026-10-06T19:00:00")   # 00:30 IST on 7 Oct
    res = cs.attribute_bbps_payments_logic(conn)
    assert res["attributed"] == 1 and "bank_debits" in res
    assert [(p["amount"], p["payment_date"], p["source"]) for p in payments(conn, "2111")] == \
        [(46088.0, "2026-10-07", "sms-bbps-attributed")]
    cs.attribute_bbps_payments_logic(conn)
    assert len(payments(conn)) == 1
