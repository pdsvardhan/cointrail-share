"""iter-203 E3 — set a category from the site, for one payment or for the payee.

PUT /transactions/{id} {category, apply_to_payee} re-files the row as a manual override and, when
asked, saves a payee rule (UPI ID / account / exact payee name) that relabels the payee's stored
rows (never a row the owner re-filed by hand) and labels future ones at ingest. Generic text is
refused with a reason (E9). Payees and amounts are made up."""
import pytest
from fastapi import HTTPException

from modules.categorizer import categorize, recategorize_with_context
from modules.txn_store import insert_transaction
from routes.transactions import (
    category_choices, delete_payee_rule, get_transaction_payee, list_payee_rules, update_transaction,
)

VPA = "worker77@okaxis"


def hdfc_sent(payee, amount="500.00", day="08/10/26"):
    return (f"Sent Rs.{amount} From HDFC Bank A/C *1111 To {payee} On {day} Ref 600000000017 "
            f"Not You? Call 18002586161/SMS BLOCK UPI to 7308080808")


def row(conn, desc, category="Transfer", merchant=None, source="sms", txn_type="debit", amount=500.0,
        date="2026-10-01", raw_text=None, confidence="medium", card="", is_duplicate=0):
    cur = conn.execute(
        "INSERT INTO transactions (amount, raw_description, merchant, category, date, source, card_last4, "
        "txn_type, confidence, raw_text, is_duplicate) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (amount, desc, merchant or desc, category, date, source, card, txn_type, confidence,
         raw_text if raw_text is not None else hdfc_sent(desc), is_duplicate),
    )
    conn.commit()
    return cur.lastrowid


def label(conn, txn_id):
    r = conn.execute("SELECT merchant, category, confidence FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    return r["category"], r["confidence"]


def test_apply_to_payee_relabels_the_payees_rows_and_future_ones(conn):
    edited = row(conn, VPA)
    earlier = row(conn, VPA, date="2026-09-01")
    copy = row(conn, VPA, date="2026-09-01", source="email", is_duplicate=1)
    hand_filed = row(conn, VPA, date="2026-08-01")
    other = row(conn, "someoneelse@okaxis")
    update_transaction(hand_filed, {"category": "Outings & misc"})

    res = update_transaction(edited, {"category": "Household help", "apply_to_payee": True})

    assert res["status"] == "updated"
    assert res["transaction"]["category"] == "Household help"
    assert res["rule"]["key_type"] == "vpa" and res["rule"]["key"] == VPA
    assert res["rule"]["category"] == "Household help" and res["rule_refused"] is None
    assert res["relabelled"] == 2 and set(res["relabelled_ids"]) == {earlier, copy}
    assert label(conn, edited) == ("Household help", "manual")
    assert label(conn, earlier) == ("Household help", "rule")
    assert label(conn, copy) == ("Household help", "rule")
    assert label(conn, hand_filed) == ("Outings & misc", "manual")      # the owner's own choice wins
    assert label(conn, other) == ("Transfer", "medium")

    new = insert_transaction(conn, amount=650.0, date="2026-10-15", source="sms", source_ref="t-1",
                             raw_description=VPA, raw_text=hdfc_sent(VPA, "650.00", "15/10/26"),
                             context="upi", link=False)
    assert (new["category"], new["confidence"]) == ("Household help", "rule")
    # a rule is the owner's decision: a later recategorise keeps it
    recategorize_with_context(conn, dry_run=False)
    assert label(conn, earlier) == ("Household help", "rule")


def test_a_payee_name_rule_matches_that_exact_payee_only(conn):
    edited = row(conn, "SVC CATERINGS")
    same = row(conn, "SVC  Caterings", date="2026-09-03")
    longer = row(conn, "SVC CATERINGS ANNEXE", date="2026-09-04")

    res = update_transaction(edited, {"category": "Food & Dining", "apply_to_payee": True})

    assert res["rule"]["key_type"] == "name" and res["rule"]["key"] == "svc caterings"
    assert label(conn, same) == ("Food & Dining", "rule")
    assert label(conn, longer) != ("Food & Dining", "rule")


def test_generic_text_is_refused_with_a_reason_and_only_the_row_changes(conn):
    bare = row(conn, "DEBIT", category="Uncategorized", raw_text="")
    placeholder = row(conn, "Account debit", category="Transfer", raw_text="")

    res = update_transaction(bare, {"category": "Household help", "apply_to_payee": True})
    res2 = update_transaction(placeholder, {"category": "Household help", "apply_to_payee": True})

    assert res["rule"] is None and "names no specific payee" in res["rule_refused"]
    assert res2["rule"] is None and res2["rule_refused"]
    assert label(conn, bare) == ("Household help", "manual")
    assert conn.execute("SELECT COUNT(*) AS n FROM payee_rules").fetchone()["n"] == 0
    assert categorize("Unknown debit", context="bank")[1] != "Household help"


def test_rules_are_listed_and_deleting_one_gives_rows_their_own_label_back(conn):
    edited = row(conn, VPA)
    earlier = row(conn, VPA, date="2026-09-01")
    update_transaction(edited, {"category": "Household help", "apply_to_payee": True})

    rules = list_payee_rules()["rules"]
    assert len(rules) == 1 and rules[0]["label"] == VPA and rules[0]["matching_transactions"] == 2

    res = delete_payee_rule(rules[0]["id"])
    assert res["status"] == "deleted" and res["relabelled"] == 1
    assert label(conn, earlier)[0] == "Transfer"                      # back to what the categorizer says
    assert label(conn, edited) == ("Household help", "manual")       # the hand-filed row stays
    assert list_payee_rules()["rules"] == []
    with pytest.raises(HTTPException):
        delete_payee_rule(rules[0]["id"])


def test_payee_lookup_tells_the_ui_what_a_rule_would_cover(conn):
    a = row(conn, VPA)
    row(conn, VPA, date="2026-09-01")
    bare = row(conn, "DEBIT", category="Uncategorized", raw_text="")

    info = get_transaction_payee(a)
    assert info["payee"] == {"key_type": "vpa", "key": VPA, "label": VPA}
    assert info["refused"] is None and info["rule"] is None and info["matching_transactions"] == 2

    update_transaction(a, {"category": "Household help", "apply_to_payee": True})
    assert get_transaction_payee(a)["rule"]["category"] == "Household help"

    nothing = get_transaction_payee(bare)
    assert nothing["payee"] is None and "names no specific payee" in nothing["refused"]


def test_category_choices_include_the_new_everyday_categories(conn):
    choices = {c["name"]: c["group"] for c in category_choices()["categories"]}
    for name in ("Household help", "Local transport", "Outings & misc"):
        assert choices[name] == "spend"
    assert choices["Transfer"] == "sent_to_people"
    assert choices["Family Remittance"] == "sent_to_people"
    assert choices["Home Construction"] == "investments"
    assert choices["Loan Repayment"] == "loans"
    assert choices["Self Transfer"] == "excluded"
    assert choices["Food & Dining"] == "spend"


def test_update_rejects_a_missing_transaction_and_an_empty_category(conn):
    with pytest.raises(HTTPException):
        update_transaction(999999, {"category": "Household help"})
    rid = row(conn, VPA)
    with pytest.raises(HTTPException):
        update_transaction(rid, {"category": "   ", "apply_to_payee": True})
