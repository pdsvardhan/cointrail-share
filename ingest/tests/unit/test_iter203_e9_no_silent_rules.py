"""iter-203 E9 — one manual correction must never silently become a broad rule.

Before iter-203, PUT /transactions/{id} called learn_from_override(), which stored the first 40
characters of the description as a 'contains' alias: re-filing one bare "DEBIT" row relabelled
every future row containing "debit", and re-filing one Swiggy order replaced the Swiggy alias, so
every later Swiggy order followed. A correction now changes that one row only; a rule exists only
when the owner asks for one (E3, "apply to all payments to this payee")."""
from modules.categorizer import categorize, recategorize_with_context
from routes.transactions import update_transaction


def row(conn, desc, category, merchant=None, source="sms", card="", txn_type="debit", amount=100.0,
        date="2026-10-01", raw_text=None, confidence="medium"):
    cur = conn.execute(
        "INSERT INTO transactions (amount, raw_description, merchant, category, date, source, card_last4, "
        "txn_type, confidence, raw_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (amount, desc, merchant or desc, category, date, source, card, txn_type, confidence, raw_text),
    )
    conn.commit()
    return cur.lastrowid


def label(conn, txn_id):
    r = conn.execute("SELECT merchant, category, confidence FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    return r["merchant"], r["category"]


def user_aliases(conn):
    return conn.execute(
        "SELECT pattern, category FROM merchant_aliases WHERE source NOT IN ('seed', 'seed_v2', 'manual')"
    ).fetchall()


def test_refiling_a_bare_debit_row_creates_no_rule_and_touches_nothing_else(conn):
    bare = row(conn, "DEBIT", "Uncategorized")
    other = row(conn, "Unknown debit", "Uncategorized")

    update_transaction(bare, {"category": "Household help"})

    assert label(conn, bare)[1] == "Household help"
    assert user_aliases(conn) == []
    assert conn.execute("SELECT COUNT(*) AS n FROM payee_rules").fetchone()["n"] == 0
    # neither a stored row nor a future one follows the single correction
    assert label(conn, other) == ("Unknown debit", "Uncategorized")
    assert categorize("Unknown debit", context="bank")[1] != "Household help"
    assert categorize("SBI loan debit", context="bank")[1] != "Household help"


def test_refiling_one_swiggy_order_changes_only_that_row(conn):
    first = row(conn, "SWIGGY", "Food & Dining", merchant="Swiggy", source="card", card="9999")
    second = row(conn, "SWIGGY", "Food & Dining", merchant="Swiggy", source="card", card="9999")

    update_transaction(first, {"category": "Groceries"})

    assert label(conn, first)[1] == "Groceries"
    assert label(conn, second) == ("Swiggy", "Food & Dining")
    assert categorize("SWIGGY", context="card") == ("Swiggy", "Food & Dining", "high")
    # the seed alias itself is untouched
    seed = conn.execute("SELECT category, source FROM merchant_aliases WHERE pattern = 'swiggy'").fetchone()
    assert (seed["category"], seed["source"]) == ("Food & Dining", "seed")


def test_a_corrected_row_is_never_relabelled_by_a_later_recategorise(conn):
    rid = row(conn, "RAMESH KUMAR", "Transfer", source="sms")
    update_transaction(rid, {"category": "Household help"})
    recategorize_with_context(conn, dry_run=False)
    assert label(conn, rid)[1] == "Household help"
