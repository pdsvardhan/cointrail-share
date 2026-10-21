"""Data hygiene — iteration 203 E12: year-late SMS rows, the bank's sample SMS, and a statement
reconcile that compares like with like. All numbers here are made up."""
from datetime import date

import services.cards_service as cs
from modules.pdf import store
from modules.pdf.parsers.base import Statement, Transaction
from modules.txn_store import insert_transaction
from routes.analytics import analytics_overview
from scripts import iter203_be1_steps as steps


def sms_row(conn, body, ts, amount, on, card="4400", desc="PAYTMQR SHOP", context="cc"):
    sid = conn.execute("INSERT INTO sms (sender, body, phone_timestamp) VALUES ('VM-BANK-S', ?, ?)", (body, ts)).lastrowid
    tid = insert_transaction(conn, amount=amount, date=on, source="sms", source_ref=str(sid), raw_description=desc,
                             raw_text=body, card_last4=card, context=context)["id"]
    return sid, tid


def date_of(conn, tid):
    return conn.execute("SELECT date FROM transactions WHERE id = ?", (tid,)).fetchone()["date"]


# --- AC1: year-late rows ----------------------------------------------------------------------------------------

def test_a_row_dated_a_year_after_its_sms_gets_its_year_back(conn, make_card):
    make_card("HDFC", "4400", statement_day=1, due_day=21)
    _, late = sms_row(conn, "Txn Rs.450.00 On Bank Card 4400 At shop@ptys by UPI On 16-02", "2025-02-16T08:00:00",
                      450.0, "2026-02-16")
    _, fine = sms_row(conn, "Txn Rs.99.00 On Bank Card 4400 At shop@ptys by UPI On 03-03", "2026-03-03T08:00:00",
                      99.0, "2026-03-03")
    dry = steps.step_repair_year_late_rows(conn, dry_run=True)
    assert [(r["id"], r["from"], r["to"]) for r in dry["rows"]] == [(late, "2026-02-16", "2025-02-16")]
    assert date_of(conn, late) == "2026-02-16"                                   # dry run wrote nothing
    steps.step_repair_year_late_rows(conn, dry_run=False)
    assert (date_of(conn, late), date_of(conn, fine)) == ("2025-02-16", "2026-03-03")
    assert steps.step_repair_year_late_rows(conn, dry_run=False)["rows"] == []      # idempotent
    assert analytics_overview(period="2026-02")["total"] == 0


# --- AC2: the bank's sample SMS and its test card ------------------------------------------------------------------

def counted(conn, period):
    o = analytics_overview(period=period)
    return round(o["total"] + sum(v["total"] for v in o["outflows"].values()), 2)


SAMPLE = "Rs.250.00 debited from a/c XX1234 on 21-03-26 to UPI/Swiggy. Avl bal Rs.12,345.67"


def test_the_sample_sms_is_excluded_and_its_test_card_removed(conn, make_card):
    make_card("Axis", "1234", name="Axis Card", active=0)
    make_card("HDFC", "4400", name="Account", active=0)
    _, test_row = sms_row(conn, SAMPLE, "2026-03-21T01:52:00", 250.0, "2026-03-21", card="1234", desc="Axis debit",
                          context="bank")
    _, real = sms_row(conn, "Rs.250.00 debited from a/c XX4400 on 21-03-26 to UPI/RAMESH. Avl bal Rs.8,100.00",
                      "2026-03-21T09:00:00", 250.0, "2026-03-21", card="4400", desc="RAMESH", context="bank")
    test_card = conn.execute("SELECT id FROM cards WHERE last4 = '1234'").fetchone()["id"]
    conn.execute("UPDATE transactions SET card_id = ? WHERE id = ?", (test_card, test_row))   # FK to the card row
    before = counted(conn, "2026-03")
    dry = steps.step_exclude_test_rows(conn, dry_run=True)
    assert dry["rows"] == [test_row] and dry["cards"] == ["1234"]
    steps.step_exclude_test_rows(conn, dry_run=False)
    r = conn.execute("SELECT is_duplicate, merge_rule, matched_txn_id FROM transactions WHERE id = ?", (test_row,)).fetchone()
    assert tuple(r) == (1, "test-message", None)                                 # flagged, not deleted
    assert counted(conn, "2026-03") == before - 250
    assert conn.execute("SELECT COUNT(*) c FROM cards WHERE last4 = '1234'").fetchone()["c"] == 0
    assert conn.execute("SELECT is_duplicate FROM transactions WHERE id = ?", (real,)).fetchone()[0] == 0
    assert steps.step_exclude_test_rows(conn, dry_run=False)["rows"] == []        # idempotent


def test_a_card_with_real_history_is_never_removed(conn, make_card):
    make_card("Axis", "1234", name="Axis Card", active=0)
    sms_row(conn, SAMPLE, "2026-03-21T01:52:00", 250.0, "2026-03-21", card="1234", desc="Axis debit", context="bank")
    sms_row(conn, "Rs.90.00 debited from a/c XX1234 on 22-03-26 to UPI/TEA. Avl bal Rs.100.00", "2026-03-22T09:00:00",
            90.0, "2026-03-22", card="1234", desc="TEA", context="bank")
    assert steps.step_exclude_test_rows(conn, dry_run=False)["cards"] == []


# --- AC3: reconcile like with like -----------------------------------------------------------------------------------

def add_statement(conn, last4, start, end, stated, due):
    card = conn.execute("SELECT id FROM cards WHERE last4=?", (last4,)).fetchone()["id"]
    return conn.execute(
        "INSERT INTO statements (card_id, month, year, total_amount, stated_total, billing_start, billing_end, due_date, "
        "due_date_source, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'bank', 'parsed')",
        (card, int(end[5:7]), int(end[:4]), stated, stated, start, end, due)).lastrowid


def line(conn, last4, on, amount, desc, credit=False, kind=None, ref=None):
    insert_transaction(conn, amount=amount, date=on, source="card", source_ref=ref or f"L-{on}-{amount}-{desc}",
                       raw_description=desc, card_last4=last4, txn_type="credit" if credit else "debit",
                       context="card", pattern_kind=kind, link=False)


def flags(conn):
    cs.reconcile_statements_logic(conn=conn)
    return {r["billing_end"]: (r["needs_review"], r["reconcile_method"], r["reconcile_gap"]) for r in conn.execute(
        "SELECT billing_end, needs_review, reconcile_method, reconcile_gap FROM statements")}


def _pdf(lines, payments, previous, purchases, credits, total, end=date(2026, 8, 22)):
    st = Statement(bank="hdfc", card_last4="4400", period_start=date(2026, 7, 23), period_end=end,
                   previous_balance=previous, purchases=purchases, payments_credits=credits, total_due=total,
                   due_date=date(2026, 9, 12))
    st.transactions, st.payments = lines, payments
    return st


def test_a_statement_whose_lines_add_up_to_its_printed_summary_is_not_flagged(conn, make_card):
    """The bank's total is a balance: last bill's leftover + this cycle. The lines dated on the
    previous close day are on THIS statement, so a date window would show an 'unexplained' gap."""
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    st = _pdf([Transaction("2026-07-22", "SWIGGY", 905.0, line_index=1),          # close day, billed now
               Transaction("2026-08-01", "AMAZON", 20263.4, line_index=2),
               Transaction("2026-08-03", "REFUND AMAZON", 3223.4, is_credit=True, line_index=3)],
              [Transaction("2026-08-03", "PAYMENT RECEIVED", 21177.0, is_credit=True, line_index=4)],
              previous=21177.11, purchases=21168.4, credits=24400.4, total=17945.11)
    store.store_statement(conn, "/data/statements/H.pdf", "hdfc", st)
    assert flags(conn)["2026-08-22"][:2] == (0, "printed-summary")


def test_sbi_emi_instalments_billed_outside_purchases_are_explained(conn, make_card):
    make_card("SBI", "4411", statement_day=19, due_day=8)
    st = Statement(bank="sbi", card_last4="4411", period_start=date(2026, 7, 20), period_end=date(2026, 8, 19),
                   previous_balance=100.0, purchases=27982.0, payments_credits=100.0, total_due=5964.28,
                   due_date=date(2026, 9, 8))
    st.transactions = [Transaction("2026-07-27", "ONLINE STORE", 26982.0, line_index=1),
                       Transaction("2026-08-01", "TEA", 1000.0, line_index=2),
                       Transaction("2026-08-19", "FP EMI 01/06", 4864.28, line_index=3, pattern_kind="emi_principal")]
    st.payments = [Transaction("2026-08-03", "PAYMENT RECEIVED", 100.0, is_credit=True, line_index=4)]
    store.store_statement(conn, "/data/statements/S.pdf", "sbi", st)
    assert flags(conn)["2026-08-19"][:2] == (0, "printed-summary")


def test_a_line_the_parser_missed_is_flagged(conn, make_card):
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    st = _pdf([Transaction("2026-08-01", "AMAZON", 1000.0, line_index=1)], [],
              previous=0.0, purchases=1905.0, credits=0.0, total=1905.0)
    store.store_statement(conn, "/data/statements/H.pdf", "hdfc", st)
    needs, method, gap = flags(conn)["2026-08-22"]
    assert (needs, method, gap) == (1, "printed-summary", 905.0)


def test_without_a_pdf_the_balance_carries_last_months_leftover(conn, make_card):
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    add_statement(conn, "4400", "2026-06-23", "2026-07-22", 10000.0, "2026-08-12")
    add_statement(conn, "4400", "2026-07-23", "2026-08-22", 8000.0, "2026-09-12")
    cs.record_card_payment(conn, "4400", 4000.0, "2026-08-05", "sms")
    line(conn, "4400", "2026-08-01", 2000.0, "AMAZON")
    assert flags(conn)["2026-08-22"][:2] == (0, "balance")
    conn.execute("UPDATE statements SET stated_total = 9000 WHERE billing_end = '2026-08-22'")
    needs, method, gap = flags(conn)["2026-08-22"]
    assert (needs, method, gap) == (1, "balance", 1000.0)


def test_unknown_previous_balance_is_not_called_a_parse_error(conn, make_card):
    """The first statement we have: the bank's total may carry a balance we never saw."""
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    add_statement(conn, "4400", "2025-07-23", "2025-08-22", 15788.0, "2025-09-12")
    line(conn, "4400", "2025-08-01", 451.0, "AMAZON")
    assert flags(conn)["2025-08-22"][:2] == (0, "cycle")
