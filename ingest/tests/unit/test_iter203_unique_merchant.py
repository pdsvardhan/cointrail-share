"""The `unique` merge rule checks the merchant — iteration 203 E11 (independent review row 20).

Same card, same amount, adjacent days, one candidate each way: `unique` merged them without looking
at the merchant, so an order that never posted (SMS) and a different purchase the next day
(statement line) became one row and a real purchase disappeared. Card numbers are made up."""
from modules import merge
from modules.txn_store import insert_transaction


def sms(conn, amount, on, desc, card="4400", ref="s1"):
    return insert_transaction(conn, amount=amount, date=on, source="sms", source_ref=ref, raw_description=desc,
                              card_last4=card, context="cc")["id"]


def line(conn, amount, on, desc, card="4400", ref="A.pdf#1"):
    return insert_transaction(conn, amount=amount, date=on, source="card", source_ref=ref, raw_description=desc,
                              card_last4=card, context="card")["id"]


def dup(conn, tid):
    r = conn.execute("SELECT is_duplicate, merge_rule FROM transactions WHERE id = ?", (tid,)).fetchone()
    return r["is_duplicate"], r["merge_rule"]


def test_two_known_different_merchants_on_adjacent_days_stay_two(conn, make_card):
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    s = sms(conn, 501.0, "2026-09-18", "SWIGGY")
    c = line(conn, 501.0, "2026-09-19", "UBER INDIA SYSTEMS")
    assert dup(conn, s) == (0, None) and dup(conn, c) == (0, None)
    assert merge.run_merge_sweep(conn)["linked"] == 0


def test_a_registered_name_still_merges_with_the_brand(conn, make_card):
    """The bank prints the merchant's legal name; the statement prints the brand: one purchase."""
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    s = sms(conn, 501.0, "2026-09-18", "ACME RETAIL VENTURES PRIVATE LIMITED")
    line(conn, 501.0, "2026-09-19", "SWIGGY")
    assert dup(conn, s) == (1, "unique")


def test_a_registered_address_the_alias_table_maps_elsewhere_still_merges(conn, make_card):
    """The bank's SMS printed the merchant's registered address, which an owner alias files as office
    rent; the statement printed the brand. Only a brand printed in the text can veto."""
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    conn.execute("INSERT INTO merchant_aliases (pattern, match_type, merchant_name, category, source) "
                 "VALUES ('internally desi', 'contains', 'Office Rent', 'Rent & Housing', 'owner')")
    conn.commit()
    s = sms(conn, 4259.0, "2025-11-23", "3rd Floor Internally desi")
    line(conn, 4259.0, "2025-11-23", "PPSL*SwiggyBangalore")
    assert dup(conn, s) == (1, "unique")


def test_the_same_brand_in_two_wordings_still_merges(conn, make_card):
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    s = sms(conn, 344.0, "2026-09-18", "BUNDL TECHNOLOGIES")
    line(conn, 344.0, "2026-09-18", "SWIGGY")
    assert dup(conn, s) == (1, "unique")


def test_existing_unique_links_between_known_different_brands_are_listed_and_undone(conn, make_card):
    make_card("HDFC", "4400", statement_day=22, due_day=12)
    s = sms(conn, 501.0, "2026-09-18", "SWIGGY")
    c = line(conn, 501.0, "2026-09-19", "UBER INDIA SYSTEMS")
    keep_s = sms(conn, 344.0, "2026-09-18", "ACME RETAIL VENTURES PRIVATE LIMITED", ref="s2")
    keep_c = line(conn, 344.0, "2026-09-19", "SWIGGY", ref="A.pdf#2")
    merge.apply_link(conn, merge._row(conn, s), merge._row(conn, c), "unique")      # as the old rule did
    conn.commit()
    assert dup(conn, s) == (1, "unique") and dup(conn, keep_s) == (1, "unique")
    review = merge.unique_links_refused(conn, unlink=False)
    assert [(r["loser"], r["winner"]) for r in review["refused"]] == [(s, c)]
    assert dup(conn, s) == (1, "unique")                                            # dry run wrote nothing
    merge.unique_links_refused(conn, unlink=True)
    assert dup(conn, s) == (0, None) and dup(conn, keep_s) == (1, "unique") and keep_c
    assert merge.run_merge_sweep(conn)["linked"] == 0                               # and the sweep leaves it
