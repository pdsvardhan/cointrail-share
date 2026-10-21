"""iter-18 statement storage: line identity, re-downloads, re-parse safety, printed bills (HP5),
M2 row repair. Uses the anonymised text fixtures; the bank's statement contract is monkeypatched."""
import os
import re
import sys
import types

import pytest

from modules.pdf import store
from modules.pdf.parsers import get_parser
from services import cards_service

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "pdf")


def stmt_of(bank, fixture, filename="statement.pdf"):
    text = open(os.path.join(FIX, fixture), encoding="utf-8").read()
    pages = re.split(r"(?m)^=== PAGE \d+ ===\n", text)[1:]
    return get_parser(bank).parse_text(pages, filename=filename)


@pytest.fixture
def bills(monkeypatch):
    calls = []

    def record_bank_statement(conn, card_last4, total_due, due_date_iso, source, min_due=None):
        calls.append((card_last4, total_due, due_date_iso, source, min_due))
        return 1
    monkeypatch.setattr(cards_service, "record_bank_statement", record_bank_statement, raising=False)
    return calls


def card_rows(conn, last4=None):
    q = "SELECT * FROM transactions WHERE source = 'card'"
    args = ()
    if last4:
        q += " AND card_last4 = ?"
        args = (last4,)
    return conn.execute(q + " ORDER BY id", args).fetchall()


def legacy_row(conn, *, amount, date, desc, card, bank="hdfc", txn_type="debit", category="Uncategorized"):
    """A row as the pre-iter-18 PDF path wrote it: no source_ref, no kind."""
    return conn.execute(
        "INSERT INTO transactions (amount, raw_description, merchant, category, date, source, bank, "
        "card_last4, txn_type, confidence) VALUES (?, ?, ?, ?, ?, 'card', ?, ?, ?, 'medium')",
        (amount, desc, desc, category, date, bank, card, txn_type)).lastrowid


# --- line identity --------------------------------------------------------------------------------

def test_each_line_is_its_own_row_and_a_reparse_adds_nothing(conn, make_card, bills):
    make_card("HDFC", "1111", statement_day=1, due_day=21)
    st = stmt_of("hdfc", "hdfc_2026_wrapped_payment.txt")
    res = store.store_statement(conn, "/data/statements/A.pdf", "hdfc", st)
    rows = card_rows(conn)
    assert res["stored"] == len(st.transactions) == len(rows) == 6
    assert len([r for r in rows if r["amount"] == 686.70]) == 2                # identical lines kept
    assert {r["source_ref"] for r in rows} == {f"A.pdf#{t.line_index}" for t in st.transactions}
    assert all(r["card_last4"] == "1111" and r["bank"] == "HDFC" for r in rows)
    assert not any(r["amount"] == 15336.0 for r in rows)                       # payment not stored
    again = store.store_statement(conn, "/data/statements/A.pdf", "hdfc", st)
    assert (again["stored"], again["exists"]) == (0, 6) and len(card_rows(conn)) == 6


def test_same_statement_downloaded_twice_is_not_stored_twice(conn, make_card, bills):
    make_card("HDFC", "1111", statement_day=1, due_day=21)
    st = stmt_of("hdfc", "hdfc_2026_wrapped_payment.txt")
    store.store_statement(conn, "/data/statements/A.pdf", "hdfc", st)
    res = store.store_statement(conn, "/data/statements/A_1.pdf", "hdfc", stmt_of("hdfc", "hdfc_2026_wrapped_payment.txt"))
    assert (res["stored"], res["matched_existing"]) == (0, 6) and len(card_rows(conn)) == 6


def test_routine_path_compares_descriptions_for_other_files(conn, make_card, bills):
    make_card("HDFC", "1111", statement_day=1, due_day=21)
    other = conn.execute(
        "INSERT INTO transactions (amount, raw_description, date, source, source_ref, card_last4, txn_type) "
        "VALUES (2826.0, 'SOMETHING ELSE', '2026-06-17', 'card', 'B.pdf#3', '1111', 'debit')").lastrowid
    st = stmt_of("hdfc", "hdfc_2026_wrapped_payment.txt")
    res = store.store_statement(conn, "/data/statements/A.pdf", "hdfc", st)
    assert res["stored"] == 6                                     # different text: not the same line
    assert conn.execute("SELECT COUNT(*) c FROM transactions WHERE amount = 2826.0").fetchone()["c"] == 2
    conn.execute("DELETE FROM transactions WHERE source_ref LIKE 'A.pdf#%'")
    res = store.store_statement(conn, "/data/statements/A.pdf", "hdfc", st, any_description=True)
    assert (res["stored"], res["matched_existing"]) == (5, 1) and other


def test_pre_iter18_rows_count_once_whatever_their_text(conn, make_card, bills):
    """The old parser collapsed the two identical lines into one row and kept the reward points in
    ICICI descriptions: the old row stands for one line, the second identical line is missing."""
    make_card("HDFC", "1111", statement_day=1, due_day=21)
    legacy_row(conn, amount=686.70, date="2026-06-15", desc="WWWBIGBASKETCOM OLD TEXT", card="1111")
    st = stmt_of("hdfc", "hdfc_2026_wrapped_payment.txt")
    res = store.store_statement(conn, "/data/statements/A.pdf", "hdfc", st)
    assert (res["stored"], res["matched_existing"]) == (5, 1)
    assert len([r for r in card_rows(conn) if r["amount"] == 686.70]) == 2
    rerun = store.store_statement(conn, "/data/statements/A.pdf", "hdfc", st)
    copy = store.store_statement(conn, "/data/statements/A_1.pdf", "hdfc", st)
    assert rerun["stored"] == copy["stored"] == 0 and len(card_rows(conn)) == 6


# --- HP5: the printed bill goes to the cards service, for the REAL card -----------------------------

def test_bill_recorded_for_the_card_behind_the_statement_alias(conn, make_card, bills):
    make_card("SBI", "4444", statement_day=19, due_day=8, pdf_card_last4="5555")
    st = stmt_of("sbi", "sbi_2026_09.txt", "1234567890125555_19092026.pdf")
    res = store.store_statement(conn, "/data/statements/1234567890125555_19092026.pdf", "sbi", st)
    assert bills == [("4444", 46088.0, "2026-10-09", "pdf-sbi", 5598.0)]
    assert res["statement"]["recorded"] and res["card_last4"] == "4444"
    rows = card_rows(conn)
    assert rows and all(r["card_last4"] == "4444" for r in rows)
    emi = [r for r in rows if r["raw_description"].startswith("FP EMI")][0]
    assert emi["kind"] == "emi_principal"
    claude = [r for r in rows if r["amount"] == 11299.11][0]
    assert (claude["currency"], claude["original_amount"], claude["fx_estimated"]) == ("USD", 118.0, 0)
    assert not any(r["raw_description"] in ("INSUFFICIENT FUNDS", "DIRECT DEBIT PAYMENT(ACH)") for r in rows)


def test_short_printed_suffix_resolves_when_no_alias_is_configured(conn, make_card, bills):
    make_card("SBI", "4444", statement_day=19, due_day=8)
    make_card("SBI", "9999", statement_day=19, due_day=8)
    st = stmt_of("sbi", "sbi_2026_09.txt", "upload-tmp123.pdf")      # uploaded: no account in name
    store.store_statement(conn, "/tmp/upload-tmp123.pdf", "sbi", st)
    assert bills[0][0] == "4444"
    assert {r["card_last4"] for r in card_rows(conn)} == {"4444"}


def test_no_due_date_no_bill_and_unknown_card_untouched(conn, make_card, bills):
    make_card("HDFC", "1111", statement_day=1, due_day=21)
    st = stmt_of("hdfc", "hdfc_2026_wrapped_payment.txt")
    st.due_date = None                                   # "Nil" — nothing is due
    res = store.store_statement(conn, "/data/statements/A.pdf", "hdfc", st)
    assert bills == [] and res["statement"]["recorded"] is False
    assert store.resolve_card(conn, "hdfc", "4321") == "4321" and store.known_card(conn, "hdfc", "4321") is None


def test_bill_failure_is_reported_and_lines_are_kept(conn, make_card, monkeypatch):
    make_card("HDFC", "1111", statement_day=1, due_day=21)

    def boom(*a, **k):
        raise RuntimeError("cards service down")
    monkeypatch.setattr(cards_service, "record_bank_statement", boom, raising=False)
    res = store.store_statement(conn, "/data/statements/A.pdf", "hdfc", stmt_of("hdfc", "hdfc_2026_wrapped_payment.txt"))
    assert res["stored"] == 6 and "cards service down" in res["statement"]["error"]


# --- card attribution repairs ---------------------------------------------------------------------

def test_icici_add_on_lines_keep_their_card_and_old_rows_move_there(conn, make_card, bills):
    make_card("ICICI", "8888", statement_day=21, due_day=10)
    make_card("ICICI", "3333", statement_day=21, due_day=10)
    old = legacy_row(conn, amount=6805.69, date="2025-09-10", desc="PROTON AG* PROTON AG GENEVA CH* 261",
                     card="8888", bank="icici")
    st = stmt_of("icici", "icici_sapphiro_addon.txt", "5241XXXXXXXX8888_200002_Retail_Sapphiro_NORM.pdf")
    res = store.store_statement(conn, "/data/statements/5241XXXXXXXX8888_200002_Retail_Sapphiro_NORM.pdf",
                                "icici", st, any_description=True)
    assert res["refiled"] == 1 and res["refiled_rows"][0]["to_card"] == "3333"
    moved = conn.execute("SELECT card_last4, source_ref FROM transactions WHERE id = ?", (old,)).fetchone()
    assert moved["card_last4"] == "3333" and moved["source_ref"].startswith("5241XXXXXXXX8888_200002")
    fee = [r for r in card_rows(conn) if r["raw_description"] == "Late Payment Fee"][0]
    assert fee["card_last4"] == "8888"                          # unknown section -> statement card
    conversion = [r for r in card_rows(conn) if r["txn_type"] == "credit" and r["amount"] == 18619.0][0]
    assert conversion["kind"] == "emi_conversion"
    assert bills == [("8888", -7280.91, "2025-10-08", "pdf-icici", 0.0)]


def test_rows_the_old_filename_map_put_on_the_wrong_card_are_moved(conn, make_card, bills):
    make_card("HDFC", "1111", statement_day=1, due_day=21)
    make_card("HDFC", "2222", statement_day=22, due_day=12)
    wrong = legacy_row(conn, amount=588.0, date="2025-08-08", desc="21:10:31 UPI-PIZZA STUDIO", card="2222")
    pay = legacy_row(conn, amount=8630.0, date="2025-08-22", txn_type="credit", category="Finance",
                     desc="07:36:54 TELE TRANSFER CREDIT (Ref# ST000000000000000000002)", card="2222")
    st = stmt_of("hdfc", "hdfc_2025_layout.txt")
    res = store.store_statement(conn, "/data/statements/old.PDF", "hdfc", st)
    assert res["refiled"] == 2 and res["stored"] == 4
    assert {r["card_last4"] for r in card_rows(conn)} == {"1111"}
    assert conn.execute("SELECT card_last4 FROM transactions WHERE id = ?", (pay,)).fetchone()["card_last4"] == "1111"
    assert len(card_rows(conn)) == 6 and wrong


# --- one-off repairs ------------------------------------------------------------------------------

def test_fix_hdfc_plus_rows(conn, make_card):
    make_card("HDFC", "1111", statement_day=1, due_day=21)
    bad = legacy_row(conn, amount=15336.0, date="2026-06-20", desc="+", card="1111")
    conn.execute("UPDATE transactions SET merge_rule = 'unique' WHERE id = ?", (bad,))
    keep = legacy_row(conn, amount=686.70, date="2026-06-15", desc="WWWBIGBASKETCOM", card="1111")
    other_bank = legacy_row(conn, amount=10.0, date="2026-06-15", desc="+", card="2222", bank="sbi")
    assert store.fix_hdfc_plus_rows(conn, dry_run=True) == 1
    assert conn.execute("SELECT txn_type FROM transactions WHERE id = ?", (bad,)).fetchone()["txn_type"] == "debit"
    assert store.fix_hdfc_plus_rows(conn) == 1
    r = conn.execute("SELECT * FROM transactions WHERE id = ?", (bad,)).fetchone()
    assert (r["txn_type"], r["kind"], r["category"], r["merge_rule"]) == ("credit", "bill_payment", "Finance", "unique")
    assert conn.execute("SELECT txn_type FROM transactions WHERE id = ?", (keep,)).fetchone()["txn_type"] == "debit"
    assert conn.execute("SELECT txn_type FROM transactions WHERE id = ?", (other_bank,)).fetchone()["txn_type"] == "debit"
    assert store.fix_hdfc_plus_rows(conn) == 0


def test_fix_legacy_payment_rows(conn):
    ach = legacy_row(conn, amount=3454.84, date="2026-06-08", txn_type="credit", category="EMI",
                     desc="DIRECT DEBIT PAYMENT(ACH)", card="4444", bank="sbi")
    bounce = legacy_row(conn, amount=3454.84, date="2026-06-08", category="Transfer",
                        desc="INSUFFICIENT FUNDS", card="4444", bank="sbi")
    bppy = legacy_row(conn, amount=6773.0, date="2026-08-12", txn_type="credit",
                      desc="BPPY PYMT DP000000000000AbCdEf (Ref# 000000000000000)", card="1111")
    fee = legacy_row(conn, amount=500.0, date="2026-06-09", desc="PAYMENT DISHONOUR FEE", card="4444", bank="sbi")
    refund = legacy_row(conn, amount=240.0, date="2026-06-24", txn_type="credit",
                        desc="Razorpay PaymentsBENGALURU", card="1111")
    assert store.fix_legacy_payment_rows(conn, dry_run=True) == 3
    assert store.fix_legacy_payment_rows(conn) == 3
    got = {r["id"]: (r["txn_type"], r["category"], r["kind"]) for r in card_rows(conn)}
    assert got[ach] == ("credit", "Finance", "bill_payment")
    assert got[bounce] == ("debit", "Finance", "bill_payment")
    assert got[bppy] == ("credit", "Finance", "bill_payment")
    assert got[fee][1] == "Uncategorized" and got[refund][1] == "Uncategorized"
    assert store.fix_legacy_payment_rows(conn) == 0


def test_reparse_dry_run_writes_nothing_then_inserts_only_missing_lines(conn, make_card, bills, monkeypatch):
    make_card("HDFC", "1111", statement_day=1, due_day=21)
    make_card("ICICI", "2222", statement_day=21, due_day=10)
    legacy_row(conn, amount=505.69, date="2026-07-21", desc="CITY FUEL STATION AAGFK1 KRISHNA IN 0",
               card="2222", bank="icici")
    legacy_row(conn, amount=15336.0, date="2026-06-20", desc="+", card="1111")
    files = {
        "/s/4315XXXXXXXX2222_100001_Retail_Amazon_NORM.pdf": ("icici", "icici_amazon_2026_08.txt"),
        "/s/4315XXXXXXXX2222_100001_Retail_Amazon_NORM_1.pdf": ("icici", "icici_amazon_2026_08.txt"),
        "/s/652926XXXXXX11_01-07-2026.pdf": ("hdfc", "hdfc_2026_wrapped_payment.txt"),
        "/s/Most Important Terms & Conditions.pdf": None,
    }
    monkeypatch.setattr(store, "statement_files", lambda directory=None: sorted(files))

    def parse_file(path):
        if files[path] is None:
            return None, {"file": os.path.basename(path), "status": "skipped"}
        bank, fixture = files[path]
        return bank, stmt_of(bank, fixture, os.path.basename(path))
    monkeypatch.setattr(store, "parse_file", parse_file)

    dry = store.reparse_statements(conn, dry_run=True)
    assert len(card_rows(conn)) == 2 and bills == []
    t = dry["totals"]
    assert (t["files"], t["skipped"], t["stored"], t["matched_existing"]) == (3, 1, 18 + 6, 1 + 19)
    assert [u["raw_description"] for u in dry["unmatched_existing"]] == ["+"]       # see fix_hdfc_plus_rows
    by_file = {f["file"]: f for f in dry["files"]}
    assert by_file["4315XXXXXXXX2222_100001_Retail_Amazon_NORM_1.pdf"]["stored"] == 0
    assert by_file["4315XXXXXXXX2222_100001_Retail_Amazon_NORM.pdf"]["statement"]["would_record"]["total_due"] == 108766.63

    real = store.reparse_statements(conn, dry_run=False)
    assert real["totals"]["stored"] == 24 and len(card_rows(conn)) == 2 + 24
    assert len(bills) == 3                                       # one per statement file, copy included
    again = store.reparse_statements(conn, dry_run=False)
    assert again["totals"]["stored"] == 0 and len(card_rows(conn)) == 26

    only = store.reparse_statements(conn, bank="hdfc", dry_run=True)
    assert [f["file"] for f in only["files"]] == ["652926XXXXXX11_01-07-2026.pdf"]


# --- decrypted working copies never land in the statements folder --------------------------------

def test_decrypt_writes_its_working_copy_to_the_temp_dir(tmp_path, monkeypatch):
    saved = []

    class FakePdf:
        def save(self, path):
            saved.append(path)
            open(path, "wb").close()

        def close(self):
            pass

    class PasswordError(Exception):
        pass

    def fake_open(path, password=None):
        if password != "right":
            raise PasswordError()
        return FakePdf()

    monkeypatch.setitem(sys.modules, "pikepdf", types.SimpleNamespace(open=fake_open, PasswordError=PasswordError))
    sys.modules.pop("modules.pdf.decrypt", None)
    from modules.pdf import decrypt
    monkeypatch.setattr(decrypt, "get_pdf_passwords", lambda: {"hdfc": ["wrong", "right"]})
    statements = tmp_path / "statements"
    statements.mkdir()
    src = statements / "stmt.pdf"
    src.write_bytes(b"%PDF")
    out = decrypt.decrypt_pdf(str(src), bank_hint="hdfc")
    try:
        assert out and os.path.dirname(out) != str(statements)
        assert sorted(os.listdir(statements)) == ["stmt.pdf"]
    finally:
        if out and os.path.exists(out):
            os.unlink(out)
    sys.modules.pop("modules.pdf.decrypt", None)
