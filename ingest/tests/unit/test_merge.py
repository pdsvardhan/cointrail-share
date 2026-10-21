"""Cross-source identity rules (modules.merge) — iter-18 H3 / M4 / HP3."""
from modules.txn_store import insert_transaction


def ins(conn, **kw):
    base = dict(raw_description=kw.pop("desc", "MERCHANT"), txn_type="debit")
    base.update(kw)
    return insert_transaction(conn, **base)


def live(conn):
    return conn.execute(
        "SELECT id, source, amount, merchant, category FROM transactions WHERE is_duplicate = 0 ORDER BY id"
    ).fetchall()


def test_sms_then_statement_line_is_one_purchase_even_when_sms_category_is_transfer(conn, make_card):
    """The audit's Zudio case: the SMS merchant looked like a person (Transfer); v2 never merged it."""
    make_card("ICICI", "0026", statement_day=21, due_day=10)
    # a shop name that reads like a person's name; upi context -> the person rule applies
    sms = ins(conn, amount=9648, date="2026-09-08", source="sms", source_ref="3890",
              desc="MANJU SRI", card_last4="0026", context="upi")
    assert sms["category"] == "Transfer"
    pdf = ins(conn, amount=9648, date="2026-09-08", source="card", source_ref="stmt.pdf#12",
              desc="MANJU SRI VIJAYAWADA IN 96", card_last4="0026", context="card")
    rows = live(conn)
    assert [r["id"] for r in rows] == [pdf["id"]]
    loser = conn.execute("SELECT is_duplicate, matched_txn_id, merge_rule FROM transactions WHERE id=?",
                         (sms["id"],)).fetchone()
    assert loser["is_duplicate"] == 1 and loser["matched_txn_id"] == pdf["id"]
    # a statement line never inherits a transfer-type guess
    assert rows[0]["category"] != "Transfer"


def test_two_identical_purchases_same_day_stay_two(conn, make_card):
    make_card("HDFC", "9434", statement_day=22, due_day=12)
    a = ins(conn, amount=100, date="2026-10-01", source="sms", source_ref="1", desc="Swiggy", card_last4="9434", context="cc")
    b = ins(conn, amount=100, date="2026-10-01", source="sms", source_ref="2", desc="Swiggy", card_last4="9434", context="cc")
    assert len(live(conn)) == 2
    # and the statement later lists both: each SMS pairs with one line
    ins(conn, amount=100, date="2026-10-01", source="card", source_ref="s#1", desc="SWIGGY BANGALORE", card_last4="9434", context="card")
    ins(conn, amount=100, date="2026-10-01", source="card", source_ref="s#2", desc="SWIGGY BANGALORE", card_last4="9434", context="card")
    rows = live(conn)
    assert len(rows) == 2 and all(r["source"] == "card" for r in rows)
    assert {a["id"], b["id"]} == {r["id"] for r in conn.execute("SELECT id FROM transactions WHERE is_duplicate=1")}


def test_same_amount_different_merchants_on_same_card_are_not_merged(conn, make_card):
    """Two SMS candidates, the statement line corroborates only one (v2 verifier case Swiggy/Uber)."""
    make_card("HDFC", "9434")
    ins(conn, amount=250, date="2026-10-02", source="sms", source_ref="10", desc="Swiggy", card_last4="9434", context="cc")
    ins(conn, amount=250, date="2026-10-02", source="sms", source_ref="11", desc="Uber India", card_last4="9434", context="cc")
    pdf = ins(conn, amount=250, date="2026-10-02", source="card", source_ref="s#9", desc="UBER INDIA SYSTEMS", card_last4="9434", context="card")
    dups = conn.execute("SELECT id, raw_description, matched_txn_id FROM transactions WHERE is_duplicate=1").fetchall()
    assert len(dups) == 1 and dups[0]["raw_description"] == "Uber India" and dups[0]["matched_txn_id"] == pdf["id"]


def test_different_cards_never_merge(conn, make_card):
    make_card("HDFC", "9434")
    make_card("HDFC", "4956")
    ins(conn, amount=500, date="2026-10-03", source="sms", source_ref="20", desc="Amazon", card_last4="9434", context="cc")
    ins(conn, amount=500, date="2026-10-03", source="card", source_ref="s#20", desc="AMAZON", card_last4="4956", context="card")
    assert len(live(conn)) == 2


def test_statement_alias_last4_is_the_same_card(conn, make_card):
    make_card("SBI", "2111", statement_day=19, due_day=8, pdf_card_last4="9181")
    ins(conn, amount=449.40, date="2026-10-16", source="email", source_ref="e1", desc="CTRLXTECHNOLOGIESPRIV", card_last4="2111", context="email-card")
    ins(conn, amount=449.40, date="2026-10-16", source="card", source_ref="s#1", desc="CTRLX TECHNOLOGIES PRIV", card_last4="9181", context="card")
    assert len(live(conn)) == 1


def test_reference_number_decides_bank_account_repeats(conn):
    """M4: five Rs 280 UPI payments to one payee on one day are five payments."""
    for i, ref in enumerate(["111", "222", "333", "444", "555"]):
        ins(conn, amount=280, date="2025-06-29", source="sms", source_ref=f"u{i}", desc="Ramesh",
            card_last4="6611", ref_no=ref, context="upi")
    assert len(live(conn)) == 5
    # the bank's alert email for one of them carries the same reference -> linked, not added
    ins(conn, amount=280, date="2025-06-29", source="email", source_ref="m1", desc="RAMESH TESTPAYEE",
        card_last4="6611", ref_no="333", context="upi")
    assert len(live(conn)) == 5
    e = conn.execute("SELECT merge_rule FROM transactions WHERE source='email'").fetchone()
    assert e["merge_rule"] == "ref"


def test_different_reference_numbers_never_merge(conn):
    ins(conn, amount=99, date="2026-10-17", source="sms", source_ref="1", desc="VIJETHA", card_last4="6611", ref_no="A1", context="upi")
    ins(conn, amount=99, date="2026-10-17", source="email", source_ref="2", desc="VIJETHA SUPER MARKETS", card_last4="6611", ref_no="B2", context="upi")
    assert len(live(conn)) == 2


def _sms(conn, sid, body, ts, sender="AD-SBICRD-S"):
    conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, is_financial, is_processed) "
                 "VALUES (?, ?, ?, ?, 1, 1)", (sid, sender, body, ts))


def test_same_sms_body_delivered_twice(conn):
    body = "Rs.432.00 debited from a/c **6611 on 17-10-26 to VPA x@ybl"
    _sms(conn, 70, body, "2026-10-17T10:00:00.100000", sender="VM-HDFCBK")
    _sms(conn, 71, body + "  ", "2026-10-17T10:00:01.300000", sender="JD-HDFCBK-S")
    ins(conn, amount=432, date="2026-10-17", source="sms", source_ref="70", desc="x@ybl", card_last4="6611", raw_text=body, context="upi")
    ins(conn, amount=432, date="2026-10-17", source="sms", source_ref="71", desc="x@ybl", card_last4="6611", raw_text=body + "  ", context="upi")
    assert len(live(conn)) == 1
    assert conn.execute("SELECT merge_rule FROM transactions WHERE is_duplicate=1").fetchone()["merge_rule"] == "same-body"


def test_identical_sms_minutes_apart_are_two_purchases(conn, make_card):
    """SBI's card alert has no time or reference: two Rs 306 Blinkit orders 5 minutes apart read the same."""
    make_card("SBI", "2111")
    body = "Rs.306.00 spent on your SBI Credit Card ending 2111 at BLINKIT on 16/10/26."
    _sms(conn, 80, body, "2026-10-16T10:00:00")
    _sms(conn, 81, body, "2026-10-16T10:05:00")
    ins(conn, amount=306, date="2026-10-16", source="sms", source_ref="80", desc="BLINKIT", card_last4="2111", raw_text=body, context="cc")
    ins(conn, amount=306, date="2026-10-16", source="sms", source_ref="81", desc="BLINKIT", card_last4="2111", raw_text=body, context="cc")
    assert len(live(conn)) == 2


def test_second_wording_of_a_billed_purchase_follows_its_statement_line(conn, make_card):
    """Spend alert + e-mandate alert 62 s apart; the first is already linked to the statement line."""
    make_card("SBI", "2111")
    spend = "Rs.399.00 spent on your SBI Credit Card ending 2111 at OPENAI *CHATGPT SUBSCR on 31/08/25."
    mandate = "Transaction of Rs.399.00 at OPENAI *CHATGPT SUBSCR against E-mandate registered by you"
    _sms(conn, 90, spend, "2025-08-31T15:41:39")
    _sms(conn, 91, mandate, "2025-08-31T15:42:41")
    ins(conn, amount=399, date="2025-08-31", source="sms", source_ref="90", desc="OPENAI *CHATGPT SUBSCR", card_last4="2111", raw_text=spend, context="cc")
    line = ins(conn, amount=399, date="2025-08-31", source="card", source_ref="s#9", desc="OPENAI *CHATGPT SUBSCR", card_last4="2111", context="card")
    ins(conn, amount=399, date="2025-08-31", source="sms", source_ref="91", desc="OPENAI *CHATGPT SUBSCR", card_last4="2111", raw_text=mandate, context="cc")
    assert [r["id"] for r in live(conn)] == [line["id"]]
    assert conn.execute("SELECT merge_rule FROM transactions WHERE source_ref='91'").fetchone()["merge_rule"] == "repeat-alert"


def test_redelivered_sms_of_a_billed_purchase_is_linked_to_the_statement_line(conn, make_card):
    make_card("HDFC", "9434")
    body = "Spent Rs.507 On HDFC Bank Card 9434 At PYU*Swiggy Food On 2026-03-21:21:07:41."
    _sms(conn, 95, body, "2026-03-21T15:37:48.308000", sender="AD-HDFCBK-S")
    _sms(conn, 96, body, "2026-03-21T15:37:48.261000", sender="AD-HDFCBK-S")
    ins(conn, amount=507, date="2026-03-21", source="sms", source_ref="95", desc="PYU*Swiggy Food", card_last4="9434", raw_text=body, context="cc")
    line = ins(conn, amount=507, date="2026-03-21", source="card", source_ref="s#5", desc="PYU*SWIGGY FOOD", card_last4="9434", context="card")
    ins(conn, amount=507, date="2026-03-21", source="sms", source_ref="96", desc="PYU*Swiggy Food", card_last4="9434", raw_text=body, context="cc")
    assert [r["id"] for r in live(conn)] == [line["id"]]


def test_fuel_surcharge_tolerance_needs_merchant(conn, make_card):
    make_card("ICICI", "0026")
    ins(conn, amount=426.63, date="2026-08-11", source="sms", source_ref="f1", desc="K V KRISHNA RAO", card_last4="0026", context="cc")
    ins(conn, amount=430.90, date="2026-08-11", source="card", source_ref="s#f", desc="K V KRISHNA RAO AAGFK1", card_last4="0026", context="card")
    assert len(live(conn)) == 1
    r = conn.execute("SELECT merge_rule FROM transactions WHERE is_duplicate=1").fetchone()
    assert r["merge_rule"] in ("tolerance", "unique") or r["merge_rule"] == "merchant"


def test_refund_credit_pairs_with_statement_credit_not_with_purchase(conn, make_card):
    make_card("HDFC", "9434")
    buy = ins(conn, amount=1511, date="2026-08-14", source="sms", source_ref="b", desc="SWIGGY", card_last4="9434", context="cc")
    ref = ins(conn, amount=1511, date="2026-08-15", source="sms", source_ref="r", desc="SWIGGY E COM", card_last4="9434",
              txn_type="credit", context="cc", pattern_kind="refund")
    assert ref["kind"] == "refund"
    assert len(live(conn)) == 2           # a refund is never a duplicate of the purchase
    ins(conn, amount=1511, date="2026-08-15", source="card", source_ref="s#r", desc="SWIGGY E COM BANGALORE",
        card_last4="9434", txn_type="credit", context="card")
    rows = live(conn)
    assert len(rows) == 2 and buy["id"] in [r["id"] for r in rows]


def test_winner_flip_repoints_existing_links(conn, make_card):
    """email arrives first, SMS second, statement last: one live row (the statement line)."""
    make_card("SBI", "2111")
    e = ins(conn, amount=306, date="2026-10-16", source="email", source_ref="e", desc="BLINKIT", card_last4="2111", context="email-card")
    s = ins(conn, amount=306, date="2026-10-16", source="sms", source_ref="s", desc="Blinkit", card_last4="2111", context="cc")
    p = ins(conn, amount=306, date="2026-10-16", source="card", source_ref="p", desc="BLINKIT GURGAON", card_last4="2111", context="card")
    rows = live(conn)
    assert [r["id"] for r in rows] == [p["id"]]
    pointers = {r["id"]: r["matched_txn_id"] for r in conn.execute("SELECT id, matched_txn_id FROM transactions WHERE is_duplicate=1")}
    assert pointers == {e["id"]: p["id"], s["id"]: p["id"]}


def test_insert_is_idempotent_per_source_ref(conn):
    a = ins(conn, amount=50, date="2026-10-10", source="sms", source_ref="900", desc="Tea", card_last4="", context="upi")
    b = ins(conn, amount=50, date="2026-10-10", source="sms", source_ref="900", desc="Tea", card_last4="", context="upi")
    assert b["status"] == "exists" and b["id"] == a["id"]


def test_a_winner_is_never_linked_to_itself_by_a_later_sweep(conn, make_card):
    """Two alerts for one transfer are linked; a later sweep over the winner must leave one live row
    (staging caught the winner marked a duplicate of itself, so the Rs 3L transfer vanished)."""
    from modules.merge import run_merge_sweep
    make_card("SBI", "4321", active=0)
    _sms(conn, 60, "Your Ac Xx4321 debited Rs.3,00,000.00 for transfer to Anita Ac Xx7777 dt 12.08.26 Ref 200000017.",
         "2026-08-12T06:00:00", sender="AD-SBYONO-S")
    _sms(conn, 61, "Your A/C XXXXX004321 Debited INR 3,00,000.00 on 12/08/26 -Transferred to Mrs. ANITA RAO.",
         "2026-08-12T06:01:00", sender="AD-CBSSBI-S")
    ins(conn, amount=300000, date="2026-08-12", source="sms", source_ref="60", desc="Anita", card_last4="4321",
        ref_no="200000017", context="bank", raw_text="a")
    ins(conn, amount=300000, date="2026-08-12", source="sms", source_ref="61", desc="Mrs. ANITA RAO",
        card_last4="4321", context="bank", raw_text="b")
    for _ in range(2):
        run_merge_sweep(conn)
    rows = conn.execute("SELECT id, is_duplicate, matched_txn_id FROM transactions").fetchall()
    assert sum(1 for r in rows if not r["is_duplicate"]) == 1
    assert all(r["matched_txn_id"] != r["id"] for r in rows)


# --- iter-18 reverify2: one template, different facts = different transactions ---------------

def test_same_template_alerts_with_different_auth_times_are_two_purchases(conn, make_card):
    """Two Rs 4,259 purchases 35 s apart (two statement lines, two alert emails): the HDFC alert
    prints the auth time, and the times differ."""
    make_card("HDFC", "9434")
    a = "Spent Rs.4259 On HDFC Bank Card 9434 At SAMPLE STORE On 2025-11-23:22:36:22.Not You?"
    b = "Spent Rs.4259 On HDFC Bank Card 9434 At SAMPLE STORE On 2025-11-23:22:36:57.Not You?"
    _sms(conn, 300, a, "2025-11-23T17:06:39", sender="JX-HDFCBK-S")
    _sms(conn, 301, b, "2025-11-23T17:07:12", sender="JD-HDFCBK-S")
    ins(conn, amount=4259, date="2025-11-23", source="sms", source_ref="300", desc="SAMPLE STORE", card_last4="9434", raw_text=a, context="cc")
    ins(conn, amount=4259, date="2025-11-23", source="sms", source_ref="301", desc="SAMPLE STORE", card_last4="9434", raw_text=b, context="cc")
    assert len(live(conn)) == 2


def test_two_sms_and_two_statement_lines_pair_one_to_one(conn, make_card):
    """The statement describes the merchant differently, so only the count can pair them: two SMS
    and two lines for one card, day and amount are the same two purchases."""
    from modules.merge import run_merge_sweep
    make_card("HDFC", "9434")
    a = "Spent Rs.4259 On HDFC Bank Card 9434 At 3RD FLOOR SAMPLE On 2025-11-23:22:36:22."
    b = "Spent Rs.4259 On HDFC Bank Card 9434 At 3RD FLOOR SAMPLE On 2025-11-23:22:36:57."
    _sms(conn, 310, a, "2025-11-23T17:06:39")
    _sms(conn, 311, b, "2025-11-23T17:07:12")
    ins(conn, amount=4259, date="2025-11-23", source="sms", source_ref="310", desc="3RD FLOOR SAMPLE", card_last4="9434", raw_text=a, context="cc")
    ins(conn, amount=4259, date="2025-11-23", source="sms", source_ref="311", desc="3RD FLOOR SAMPLE", card_last4="9434", raw_text=b, context="cc")
    l1 = ins(conn, amount=4259, date="2025-11-23", source="card", source_ref="s#42", desc="EMI PPSL*SWIGGYBANGALORE", card_last4="9434", context="card")
    l2 = ins(conn, amount=4259, date="2025-11-23", source="card", source_ref="s#43", desc="EMI PPSL*SWIGGYBANGALORE", card_last4="9434", context="card")
    run_merge_sweep(conn)
    assert {r["id"] for r in live(conn)} == {l1["id"], l2["id"]}
    winners = [r["matched_txn_id"] for r in conn.execute("SELECT matched_txn_id FROM transactions WHERE source='sms'")]
    assert sorted(winners) == sorted([l1["id"], l2["id"]])


def test_three_sms_two_lines_is_not_paired_by_count(conn, make_card):
    from modules.merge import run_merge_sweep
    make_card("HDFC", "9434")
    for i, t in enumerate(["10:00:01", "10:00:40", "10:01:20"]):
        body = f"Spent Rs.999 On HDFC Bank Card 9434 At SHOP On 2025-11-24:{t}."
        _sms(conn, 320 + i, body, f"2025-11-24T04:3{i}:00")
        ins(conn, amount=999, date="2025-11-24", source="sms", source_ref=str(320 + i), desc="SHOP", card_last4="9434", raw_text=body, context="cc")
    for n in (1, 2):
        ins(conn, amount=999, date="2025-11-24", source="card", source_ref=f"s#{n}", desc="EMI XYZ", card_last4="9434", context="card")
    run_merge_sweep(conn)
    assert conn.execute("SELECT COUNT(*) c FROM transactions WHERE merge_rule='count'").fetchone()["c"] == 0


def test_same_template_card_alerts_with_different_limits_are_two_purchases(conn, make_card):
    make_card("Axis", "9320")
    a = "Spent INR 49 Axis Bank Card no. XX9320 08-10-25 08:58:13 IST NETFLIX Avl Limit: INR 10000.00"
    b = "Spent INR 49 Axis Bank Card no. XX9320 08-10-25 09:01:13 IST NETFLIX Avl Limit: INR 9951.00"
    _sms(conn, 330, a, "2025-10-08T03:28:20", sender="AX-AXISBK-S")
    _sms(conn, 331, b, "2025-10-08T03:31:20", sender="AX-AXISBK-S")
    ins(conn, amount=49, date="2025-10-08", source="sms", source_ref="330", desc="NETFLIX", card_last4="9320", raw_text=a, context="cc")
    ins(conn, amount=49, date="2025-10-08", source="sms", source_ref="331", desc="NETFLIX", card_last4="9320", raw_text=b, context="cc")
    assert len(live(conn)) == 2


def test_legacy_row_without_stored_ref_never_merges_a_different_ref(conn):
    """A pre-iter-18 row has ref_no NULL but its text prints the reference; a second Rs 280
    payment with another reference is another payment, before and after a sweep."""
    from modules.merge import run_merge_sweep, backfill_sms_refs
    a = "Sent Rs.280.00\nFrom HDFC Bank A/C x1234\nTo RAMESH\nOn 29/06/25\nRef 300000000001\nNot You?"
    b = "Sent Rs.280.00\nFrom HDFC Bank A/C x1234\nTo RAMESH\nOn 29/06/25\nRef 300000000002\nNot You?"
    _sms(conn, 340, a, "2025-06-28T19:59:55", sender="AD-HDFCBK-S")
    _sms(conn, 341, b, "2025-06-28T20:00:13", sender="AD-HDFCBK-S")
    first = ins(conn, amount=280, date="2025-06-29", source="sms", source_ref="340", desc="RAMESH", card_last4="1234",
                raw_text=a, ref_no=None, context="upi")
    conn.execute("UPDATE transactions SET ref_no = NULL WHERE id = ?", (first["id"],))
    ins(conn, amount=280, date="2025-06-29", source="sms", source_ref="341", desc="RAMESH", card_last4="1234",
        raw_text=b, ref_no="300000000002", context="upi")
    run_merge_sweep(conn)
    assert len(live(conn)) == 2
    assert backfill_sms_refs(conn, dry_run=False)["filled"] == 1
    run_merge_sweep(conn)
    refs = {r["ref_no"] for r in conn.execute("SELECT ref_no FROM transactions")}
    assert refs == {"300000000001", "300000000002"} and len(live(conn)) == 2


def test_atm_withdrawal_reversal_and_second_withdrawal(conn):
    """Withdraw, 'ATM Txn Reversed', withdraw again two minutes later at the next machine: the
    second withdrawal is real (the balance only matches because the first was reversed)."""
    w1 = "Withdrawn Rs.2300 From HDFC Bank Card x3605 At +MAIN ROAD On 2025-08-27:16:02:22 Bal Rs.97536.56"
    rv = "ATM Txn Reversed:Rs.2300 reversed to your HDFC Bank Card 3605 At +MAIN ROAD On 2025-08-27:16:03:08."
    w2 = "Withdrawn Rs.2300 From HDFC Bank Card x3605 At +2ND CD AT MAIN RD On 2025-08-27:16:04:21 Bal Rs.97536.56"
    _sms(conn, 350, w1, "2025-08-27T10:32:29", sender="AD-HDFCBK-S")
    _sms(conn, 351, rv, "2025-08-27T10:33:14", sender="AD-HDFCBK-S")
    _sms(conn, 352, w2, "2025-08-27T10:34:29", sender="AD-HDFCBK-S")
    ins(conn, amount=2300, date="2025-08-27", source="sms", source_ref="350", desc="ATM MAIN ROAD", card_last4="3605", raw_text=w1, context="atm")
    ins(conn, amount=2300, date="2025-08-27", source="sms", source_ref="351", desc="ATM MAIN ROAD", card_last4="3605", raw_text=rv,
        txn_type="credit", context="atm")
    ins(conn, amount=2300, date="2025-08-27", source="sms", source_ref="352", desc="ATM MAIN ROAD", card_last4="3605", raw_text=w2, context="atm")
    rows = conn.execute("SELECT txn_type, COUNT(*) c FROM transactions WHERE is_duplicate=0 GROUP BY txn_type").fetchall()
    assert {r["txn_type"]: r["c"] for r in rows} == {"debit": 2, "credit": 1}


def test_credit_alerts_from_two_vpas_are_two_credits(conn):
    a = "Credit Alert!\nRs.2540.00 credited to HDFC Bank A/c XX1234 on 28-02-26 from VPA alice@okhdfcbank (UPI 400000000001)"
    b = "Credit Alert!\nRs.2540.00 credited to HDFC Bank A/c XX1234 on 28-02-26 from VPA 9000000000@ybl (UPI 400000000002)"
    _sms(conn, 360, b, "2026-02-27T19:24:51", sender="VM-HDFCBK-S")
    _sms(conn, 361, a, "2026-02-27T19:27:30", sender="JM-HDFCBK-S")
    first = ins(conn, amount=2540, date="2026-02-28", source="sms", source_ref="360", desc="9000000000@ybl", card_last4="1234",
                raw_text=b, txn_type="credit", context="upi")
    conn.execute("UPDATE transactions SET ref_no = NULL WHERE id = ?", (first["id"],))
    ins(conn, amount=2540, date="2026-02-28", source="sms", source_ref="361", desc="alice@okhdfcbank", card_last4="1234",
        raw_text=a, txn_type="credit", context="upi")
    assert len(live(conn)) == 2


def test_standing_instruction_alert_still_links_to_the_spend_alert(conn, make_card):
    """ICICI SI alert + spend alert for one Rs 1,499 charge: two alert families, no conflicting fact."""
    make_card("ICICI", "0026")
    spend = "INR 1,499.00 spent using ICICI Bank Card XX0026 on 24-May-26 on Amazon. Avl Limit: INR 50,000.00."
    si = ("We have successfully processed payment of INR 1499.00 to Merchant Amazon, as per Standing "
          "Instruction on 24/05/2026 for ICICI Bank Credit Card 0026.")
    _sms(conn, 370, spend, "2026-05-24T16:24:30", sender="JX-ICICIT-S")
    _sms(conn, 371, si, "2026-05-24T16:25:04", sender="JX-ICICIT-T")
    ins(conn, amount=1499, date="2026-05-24", source="sms", source_ref="370", desc="Amazon", card_last4="0026", raw_text=spend, context="cc")
    ins(conn, amount=1499, date="2026-05-24", source="sms", source_ref="371", desc="Amazon", card_last4="0026", raw_text=si, context="cc")
    assert len(live(conn)) == 1
    assert conn.execute("SELECT merge_rule FROM transactions WHERE is_duplicate=1").fetchone()["merge_rule"] == "repeat-alert"


def test_one_rrn_on_two_account_aliases_is_one_withdrawal(conn):
    """HDFC's UPI-ATM SMS names the account 'A/c 3010', its alert email '**4400', same RRN."""
    ins(conn, amount=2500, date="2026-03-05", source="sms", source_ref="a1", desc="HDFC ATM ATM", card_last4="3010",
        ref_no="554600000001", context="atm")
    ins(conn, amount=2500, date="2026-03-05", source="email", source_ref="a2", desc="HDFC ATM", card_last4="4400",
        ref_no="554600000001", context="atm")
    assert len(live(conn)) == 1


def test_a_short_reference_never_links_two_accounts(conn):
    ins(conn, amount=500, date="2026-03-05", source="sms", source_ref="b1", desc="X", card_last4="3010",
        ref_no="123456789", context="bank")
    ins(conn, amount=500, date="2026-03-05", source="email", source_ref="b2", desc="Y", card_last4="4400",
        ref_no="123456789", context="bank")
    assert len(live(conn)) == 2


# --- a new copy must agree with the whole group, not only its winner ------------------------------

def _yono(i, ref):
    return f"Your Ac Xx4321 debited Rs.50,000.00 for transfer to Ravi Ac Xx7777 dt 12.10.26 Ref {ref}. YONO SBI"


def _cbs(bal):
    return f"Your A/C XXXXX004321 Debited INR 50,000.00 on 12/10/26 -Transferred to Mr. RAVI KUMAR. Avl Balance INR {bal}-SBI"


def _sbi(conn, sid, body, ts, sender):
    _sms(conn, sid, body, ts, sender=sender)
    ins(conn, amount=50000, date="2026-10-12", source="sms", source_ref=str(sid), desc="RAVI KUMAR",
        card_last4="4321", raw_text=body, context="bank")


def test_second_same_day_transfer_reported_core_banking_first_stays_two(conn, make_card):
    make_card("SBI", "4321", active=0)
    _sbi(conn, 400, _yono(1, "300000000101"), "2026-10-12T04:30:00", "AD-SBYONO-S")
    _sbi(conn, 401, _cbs("1,11,111.11"), "2026-10-12T04:30:30", "AD-CBSSBI-S")
    assert len(live(conn)) == 1
    _sbi(conn, 402, _cbs("61,111.11"), "2026-10-12T08:30:00", "AD-CBSSBI-S")
    _sbi(conn, 403, _yono(2, "300000000102"), "2026-10-12T08:30:40", "AD-SBYONO-S")
    from modules.merge import run_merge_sweep
    run_merge_sweep(conn)
    assert len(live(conn)) == 2


def test_second_transfer_reported_only_by_core_banking_is_not_swallowed(conn, make_card):
    make_card("SBI", "4321", active=0)
    _sbi(conn, 410, _yono(1, "300000000201"), "2026-10-12T04:30:00", "AD-SBYONO-S")
    _sbi(conn, 411, _cbs("1,11,111.11"), "2026-10-12T04:30:30", "AD-CBSSBI-S")
    _sbi(conn, 412, _cbs("61,111.11"), "2026-10-12T09:12:00", "AD-CBSSBI-S")
    assert len(live(conn)) == 2
    from modules.merge import run_merge_sweep
    run_merge_sweep(conn)                      # the hourly sweep must not absorb it either
    run_merge_sweep(conn)
    assert len(live(conn)) == 2


# --- count rule: statements are complete -------------------------------------------------------------

def _spend(conn, sid, t):
    body = f"Spent Rs.777 On HDFC Bank Card 9434 At SAMPLE On 2025-11-24:{t}."
    _sms(conn, sid, body, f"2025-11-24T04:{sid % 60:02d}:00", sender="AD-HDFCBK-S")
    ins(conn, amount=777, date="2025-11-24", source="sms", source_ref=str(sid), desc="SAMPLE", card_last4="9434",
        raw_text=body, context="cc")


def test_lines_arriving_one_by_one_pair_every_alert(conn, make_card):
    """2 SMS, then 3 statement lines one at a time: both SMS end on lines, 3 rows live."""
    from modules.merge import run_merge_sweep
    make_card("HDFC", "9434")
    _spend(conn, 420, "10:00:01")
    _spend(conn, 421, "10:00:40")
    for n in (1, 2, 3):
        ins(conn, amount=777, date="2025-11-24", source="card", source_ref=f"s#{n}", desc="EMI XYZ",
            card_last4="9434", context="card")
    run_merge_sweep(conn)
    rows = live(conn)
    assert len(rows) == 3 and all(r["source"] == "card" for r in rows)


def test_a_line_posted_the_next_day_still_pairs(conn, make_card):
    from modules.merge import run_merge_sweep
    make_card("HDFC", "9434")
    _spend(conn, 430, "10:00:01")
    _spend(conn, 431, "10:00:40")
    ins(conn, amount=777, date="2025-11-24", source="card", source_ref="s#1", desc="EMI XYZ", card_last4="9434", context="card")
    ins(conn, amount=777, date="2025-11-25", source="card", source_ref="s#2", desc="EMI XYZ", card_last4="9434", context="card")
    run_merge_sweep(conn)
    rows = live(conn)
    assert len(rows) == 2 and all(r["source"] == "card" for r in rows)


def test_one_yono_standing_instruction_and_its_core_banking_alert_are_one_row(conn, make_card):
    """Both alerts name the owner as payee ('Self Transfer' on both): that corroborates."""
    make_card("SBI", "4321", active=0)
    yono = "SBI Ac X4321 debited for SI Fund Transfer of Rs.45,000.00 to . Ac X0001 on 24.02.26 Ref 300000000301. YONO, SBI"
    cbs = "Your A/C XXXXX004321 Debited INR 45,000.00 on 24/02/26 -Transferred to Mr. SAM. Avl Balance INR 99,219.17-SBI"
    _sms(conn, 440, yono, "2026-02-24T04:56:44", sender="AX-SBYONO-S")
    _sms(conn, 441, cbs, "2026-02-24T04:57:20", sender="AD-CBSSBI-S")
    for sid, body, desc in ((440, yono, "Ac X0001"), (441, cbs, "Mr. SAM")):
        r = ins(conn, amount=45000, date="2026-02-24", source="sms", source_ref=str(sid), desc=desc, card_last4="4321",
                raw_text=body, context="bank")
        conn.execute("UPDATE transactions SET merchant = 'Self Transfer', category = 'Self Transfer' WHERE id = ?", (r["id"],))
    from modules.merge import run_merge_sweep
    run_merge_sweep(conn)
    assert len(live(conn)) == 1


def test_an_old_group_holding_two_purchases_is_repaired_by_the_sweep(conn, make_card):
    """The old matcher linked the 11 Apr and the 12 Apr Rs 49 SMS to the 12 Apr statement line and
    the 12 Apr email to the 11 Apr line; the 11 Apr email then had no consistent group to join."""
    from modules.merge import repair_inconsistent_groups, run_merge_sweep
    make_card("Axis", "9320")
    times = {"11": "08:58:13", "12": "06:57:19"}
    lines, sms, mails = {}, {}, {}
    for day, t in times.items():
        lines[day] = ins(conn, amount=49, date=f"2026-04-{day}", source="card", source_ref=f"st#{day}",
                         desc="AIRTEL PAYMENTS BANK L", card_last4="9320", context="card", link=False)["id"]
        body = f"Spent INR 49 Axis Bank Card no. XX9320 {day}-04-26 {t} IST AIRTEL PAYM Avl Limit: INR 1594{day}"
        _sms(conn, 500 + int(day), body, f"2026-04-{day}T03:00:00", sender="AX-AXISBK-S")
        sms[day] = ins(conn, amount=49, date=f"2026-04-{day}", source="sms", source_ref=str(500 + int(day)),
                       desc="AIRTEL PAYM", card_last4="9320", raw_text=body, context="cc", link=False)["id"]
        mails[day] = ins(conn, amount=49, date=f"2026-04-{day}", source="email", source_ref=f"m{day}", desc="AIRTEL PAYM",
                         card_last4="9320", raw_text=f"INR 49 spent on credit card no. XX9320 | {day}-04-2026 {t}",
                         context="email-card", link=False)["id"]
    link = "UPDATE transactions SET is_duplicate = 1, matched_txn_id = ? WHERE id = ?"
    conn.execute(link, (lines["12"], sms["11"]))
    conn.execute(link, (lines["12"], sms["12"]))
    conn.execute(link, (lines["11"], mails["12"]))
    conn.commit()
    assert repair_inconsistent_groups(conn, dry_run=False)["inconsistent_groups"] == 1
    run_merge_sweep(conn)
    assert {r["id"] for r in live(conn)} == set(lines.values())
    winner = {r["id"]: r["matched_txn_id"] for r in conn.execute("SELECT id, matched_txn_id FROM transactions")}
    assert winner[sms["11"]] == winner[mails["11"]] == lines["11"]
    assert winner[sms["12"]] == winner[mails["12"]] == lines["12"]
    assert repair_inconsistent_groups(conn, dry_run=True)["inconsistent_groups"] == 0
