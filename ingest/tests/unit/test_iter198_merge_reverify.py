"""Iteration-198 re-verify (2026-10-20), HP3: guessed SMS <-> email links yield to evidence.

Two Rs 50,000 transfers on one SBI account, alerted by YONO and core banking (SMS and mail). The
verifier's repro cases R3 / R4 and a reference stamped across a far guess. Helpers and wordings are
the bank-alert tests' (people's names replaced)."""
import pytest

from test_email_bank_alerts import _cbs_sms, _ce, _live, _yono_sms, _ye, add_email, run


def _sweep2(conn):
    from modules.merge import run_merge_sweep
    run_merge_sweep(conn)
    run_merge_sweep(conn)


def test_r3_second_transfers_mail_first_then_three_sms(conn, make_card):
    """T2's core-banking mail is stored first; T1's YONO SMS then takes it by `unique` (30 min apart).
    When T1's own core-banking SMS and T2's YONO SMS arrive, the far copy must yield."""
    make_card("SBI", "4321", name="Savings", active=0)
    add_email(conn, *_ce("61,111.11", "10:47:56"))
    run(conn)
    _yono_sms(conn, 1003, "600000101", "2026-10-12T04:47:32")
    _cbs_sms(conn, 1004, "1,11,111.11", "2026-10-12T04:48:00")
    _yono_sms(conn, 1005, "600000102", "2026-10-12T05:17:32")
    _sweep2(conn)
    assert _live(conn) == 2


def test_r4_two_transfers_90_seconds_apart_mails_first(conn, make_card):
    """90 s apart, mails first: T2's YONO mail (10:19:06) and T1's core-banking mail (10:17:56) pair as
    same-alert twins (a guess). T1's core-banking SMS then prints that mail's balance: the guess yields."""
    make_card("SBI", "4321", name="Savings", active=0)
    for mail in (_ye("600000102", "10:19:06"), _ce("1,11,111.11", "10:17:56")):
        add_email(conn, *mail)
    run(conn)
    _yono_sms(conn, 1006, "600000101", "2026-10-12T04:47:32")
    _cbs_sms(conn, 1007, "1,11,111.11", "2026-10-12T04:48:00")
    _cbs_sms(conn, 1008, "61,111.11", "2026-10-12T04:49:30")
    _sweep2(conn)
    assert _live(conn) == 2


def test_a_far_guess_does_not_lend_its_reference(conn, make_card):
    """T1 seen only by its YONO mail (10:17, ref ...101); T2 2h13m later by its core-banking SMS, then
    its YONO and core-banking mails. The SMS takes T1's mail by `unique` (nothing nearer yet) — that
    guess must not stamp T1's reference on T2's SMS, or T1 'proves' itself onto T2."""
    make_card("SBI", "4321", name="Savings", active=0)
    add_email(conn, *_ye("600000101", "10:17:36"))
    run(conn)
    _cbs_sms(conn, 1030, "61,111.11", "2026-10-12T07:01:00")      # 12:31:00 IST
    sms_row = conn.execute("SELECT * FROM transactions WHERE source = 'sms'").fetchone()
    assert not (sms_row["ref_no"] or "")
    for mail in (_ce("61,111.11", "12:30:56"), _ye("600000102", "12:30:36")):
        add_email(conn, *mail)
    run(conn)
    _sweep2(conn)
    assert _live(conn) == 2


def test_payees_conflict_reads_names_not_account_only_payees():
    from modules.merge import _payees_conflict
    a = {"merchant": "Asha Rani", "raw_description": "Asha Rani"}
    b = {"merchant": "Transferred to Mr. RAVI KUMAR", "raw_description": "Transferred to Mr. RAVI KUMAR"}
    assert _payees_conflict(a, b)
    acct_only = {"merchant": "TRANSFER TO AC X1234", "raw_description": "TRANSFER TO AC X1234"}
    assert not _payees_conflict(a, acct_only)


def test_a_card_spends_late_email_still_merges_hours_later(conn, make_card):
    """Distance alone never refuses a pairing: HDFC sends some card-spend mails hours after the SMS
    (41 genuine pairs more than 10 min apart in the live data). One purchase, SMS at 22:52, mail at
    03:10 the next morning: one row."""
    from modules.txn_store import insert_transaction
    make_card("HDFC", "4434")
    body = "Spent Rs.932 On HDFC Bank Card 4434 At SAMPLE FOODS On 2026-10-17:22:52:32.Not You?"
    conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, is_financial, is_processed) VALUES (?, ?, ?, ?, 1, 1)",
                 (1040, "JD-HDFCBK-S", body, "2026-10-17T17:22:40"))
    insert_transaction(conn, amount=932, date="2026-10-17", source="sms", source_ref="1040",
                       raw_description="SAMPLE FOODS", card_last4="4434", raw_text=body, context="cc")
    add_email(conn, "alerts@hdfcbank.bank.in", "A payment was made using your Credit Card",
              "HDFC BANK --> Dear Customer, Greetings from HDFC Bank. We would like to inform you that Rs. 932.00 has "
              "been debited from your HDFC Bank Credit Card ending 4434 towards SAMPLE FOODS on 17 Oct, 2026 at 22:52:32 . "
              "To check your available balance", "Sun, 18 Oct 2026 03:10:50 +0530")
    run(conn)
    _sweep2(conn)
    assert _live(conn) == 1


def test_two_close_card_spends_with_mails_hours_late_keep_their_own_mail(conn, make_card):
    """Two Rs 3,141 purchases 35 s apart, both mails 4 h late. Each SMS is the 'nearer alert' that
    conflicts with the other purchase's mail, but each mail prints its own SMS's time: proof beats
    distance, so each mail joins its own SMS (two rows, never three or four)."""
    from modules.merge import _alert_text, _body_facts, _seconds
    from modules.txn_store import insert_transaction
    make_card("HDFC", "4434")
    for sid, t, ts in ((1050, "22:36:22", "2025-11-12T17:06:22"), (1051, "22:36:57", "2025-11-12T17:06:57")):
        body = f"Spent Rs.3141 On HDFC Bank Card 4434 At SAMPLE STORE On 2025-11-12:{t}.Not You?"
        conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, is_financial, is_processed) VALUES (?, ?, ?, ?, 1, 1)",
                     (sid, "JX-HDFCBK-S", body, ts))
        insert_transaction(conn, amount=3141, date="2025-11-12", source="sms", source_ref=str(sid),
                           raw_description="SAMPLE STORE", card_last4="4434", raw_text=body, context="cc")
    for t, hdr in (("22:36:57", "Thu, 13 Nov 2025 02:40:10 +0530"), ("22:36:22", "Thu, 13 Nov 2025 02:40:05 +0530")):
        add_email(conn, "alerts@hdfcbank.bank.in", "A payment was made using your Credit Card",
                  "HDFC BANK --> Dear Customer, Greetings from HDFC Bank. We would like to inform you that Rs. 3141.00 has "
                  f"been debited from your HDFC Bank Credit Card ending 4434 towards SAMPLE STORE on 12 Nov, 2025 at {t} . "
                  "To check your available balance", hdr)
    run(conn)
    _sweep2(conn)
    assert _live(conn) == 2
    groups = {}
    for r in conn.execute("SELECT * FROM transactions WHERE is_duplicate = 1"):
        groups.setdefault(r["matched_txn_id"], []).append(_seconds(_body_facts(_alert_text(conn, r))["time"]))
    for head, times in groups.items():
        own = _seconds(_body_facts(_alert_text(conn, conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (head,)).fetchone()))["time"])
        assert all(abs(t - own) <= 5 for t in times)


def test_far_guess_repair_step_undoes_a_far_guess_and_re_pairs(conn, make_card):
    """History made by the old rules: T1's YONO SMS took T2's core-banking mail by `unique` 30 min
    away, leaving T1's core-banking SMS and T2's YONO SMS live (3 rows for 2 transfers)."""
    from scripts import iter203_f198_steps as steps
    from modules import merge
    from modules.txn_store import insert_transaction
    make_card("SBI", "4321", name="Savings", active=0)
    add_email(conn, *_ce("61,111.11", "10:47:56"))
    run(conn)
    mail = conn.execute("SELECT * FROM transactions WHERE source = 'email'").fetchone()
    for sid, ts, kind, val in ((1060, "2026-10-12T04:47:32", "Y", "600000101"),
                               (1061, "2026-10-12T04:48:00", "C", "1,11,111.11"),
                               (1062, "2026-10-12T05:17:32", "Y", "600000102")):
        if kind == "Y":
            body = f"Your Ac Xx4321 debited Rs.50,000.00 for transfer to Asha Ac Xx7777 dt 12.10.26 Ref {val}. YONO SBI"
            sender, desc, ref = "AD-SBYONO-S", "Asha", val
        else:
            body = (f"Your A/C XXXXX004321 Debited INR 50,000.00 on 12/10/26 -Transferred to Mrs. ASHA RANI. "
                    f"Avl Balance INR {val}-SBI")
            sender, desc, ref = "AD-CBSSBI-S", "Mrs. ASHA RANI", None
        conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, is_financial, is_processed) VALUES (?, ?, ?, ?, 1, 1)",
                     (sid, sender, body, ts))
        insert_transaction(conn, amount=50000, date="2026-10-12", source="sms", source_ref=str(sid), raw_description=desc,
                           card_last4="4321", raw_text=body, ref_no=ref, context="bank", link=False)
    ys1 = conn.execute("SELECT * FROM transactions WHERE source_ref = '1060'").fetchone()
    merge.apply_link(conn, ys1, mail, "unique")                  # the old rules' far guess
    conn.commit()
    assert _live(conn) == 3
    dry = steps.step_far_guess_repair(conn, dry_run=True)
    assert [g["copy"] for g in dry["far_guesses_undone"]] == [mail["id"]] and dry["live_after"] == 2
    assert _live(conn) == 3                                       # a dry run writes nothing
    real = steps.step_far_guess_repair(conn, dry_run=False)
    assert real["live_after"] == 2 and _live(conn) == 2
    assert steps.step_far_guess_repair(conn, dry_run=True)["far_guesses_undone"] == []


# --- round 9 (2026-10-21): payee names only from transfer wording; a freed copy goes where its balance is --

def _sms_via_parser(conn, sid, sender, body, ts):
    """Store an SMS and run it through the real path (parser, categoriser, insert, link)."""
    from modules.sms.processing import process_sms_row
    conn.execute("INSERT INTO sms (id, sender, body, phone_timestamp, received_at, is_financial, is_processed) "
                 "VALUES (?, ?, ?, ?, ?, 1, 0)", (sid, sender, body, ts, ts.replace("T", " ")))
    conn.commit()
    process_sms_row(conn, conn.execute("SELECT * FROM sms WHERE id = ?", (sid,)).fetchone())


AMC_SMS = ("Dear Customer, Your A/C ending with 4321 has been debited for INR 177.0 on 11-12-25 towards annual "
           "maintenance charges for your SBI Debit Card. -SBI")
AMC_MAIL = ("cbsalerts.sbi@alerts.sbi.co.in", "CBSSBI ALERT",
            "Greetings from SBI ! Your A/C XXXXX004321 has a debit by transfer of Rs 177.00 on 11/12/25. Avl Bal "
            "Rs 1,234.56-SBI Please do not reply to this auto generated email.", "Thu, 11 Dec 2025 09:02:22 +0530")


@pytest.mark.parametrize("order, sms_ts", [
    ("mail first", "2025-12-12T17:32:22"),          # the SMS 38 h after the mail (as on the live data)
    ("sms first", "2025-12-12T17:32:22"),
    ("sms first", "2025-12-11T03:30:22"),           # 2 min apart
])
def test_a_bank_charge_sms_and_its_core_banking_mail_are_one_debit(conn, make_card, order, sms_ts):
    """'towards annual maintenance charges' and the parser's 'SBI ACCOUNT TRANSACTION' are no payee names:
    the transfer payee check must not keep this SMS and its mail apart (round 9: 2 live, the charge counted twice)."""
    make_card("SBI", "4321", name="Savings", active=0)
    if order == "mail first":
        add_email(conn, *AMC_MAIL)
        run(conn)
        _sms_via_parser(conn, 1080, "VM-SBIBNK-S", AMC_SMS, sms_ts)
    else:
        _sms_via_parser(conn, 1080, "VM-SBIBNK-S", AMC_SMS, sms_ts)
        add_email(conn, *AMC_MAIL)
        run(conn)
    _sweep2(conn)
    assert _live(conn) == 1


def test_transfer_payee_names_come_only_from_transfer_wording():
    from modules.merge import transfer_payees_conflict
    asha = {"source": "sms", "raw_description": "Asha", "merchant": "Asha", "category": "Transfer",
            "raw_text": "Your Ac Xx4321 debited Rs.50,000.00 for transfer to Asha Ac Xx7777 dt 12.10.26 Ref 600000101."}
    ravi = {"source": "email", "raw_description": "Transferred to Mr. RAVI KUMAR", "merchant": "Ravi Kumar",
            "category": "Transfer", "raw_text": "CBSSBI ALERT | Your A/C XXXXX004321 Debited INR 50,000.00 on "
                                                "12/10/26 -Transferred to Mr. RAVI KUMAR. Avl Balance INR 61,111.11"}
    amc = {"source": "sms", "raw_description": "annual maintenance charges", "merchant": "SBI", "category":
           "Rent & Housing", "raw_text": AMC_SMS}
    fallback = {"source": "email", "raw_description": "SBI ACCOUNT TRANSACTION", "merchant": "SBI ACCOUNT TRANSACTION",
                "category": "Transfer", "raw_text": "CBSSBI ALERT | " + AMC_MAIL[2]}
    assert transfer_payees_conflict(asha, ravi)
    assert not transfer_payees_conflict(amc, fallback)
    assert not transfer_payees_conflict(asha, fallback)


def test_d1_first_transfer_seen_only_by_its_yono_mail(conn, make_card):
    """Round 9 D1 ('90s|rt-old|YE|YS+CS+YE+CE'): T1 seen only by its YONO mail; T2 fully alerted 90 s
    later. T2's core-banking mail prints T2's core-banking SMS's balance: it joins that group (which
    already holds T2's YONO mail), never T1's mail 110 s away. 2 rows."""
    make_card("SBI", "4321", name="Savings", active=0)
    _yono_sms(conn, 1090, "600000102", "2026-10-12T04:49:02")
    _cbs_sms(conn, 1091, "61,111.11", "2026-10-12T04:49:30")
    for mail in (_ye("600000101", "10:17:36"), _ye("600000102", "10:19:06"), _ce("61,111.11", "10:19:26")):
        add_email(conn, *mail)
    run(conn)
    _sweep2(conn)
    assert _live(conn) == 2


def test_d2_second_transfers_core_banking_sms_joined_the_first_transfer(conn, make_card):
    """Round 9 D2 ('90s|rt-old|YS+YE|CS+YE+CE'): T2's core-banking SMS joins T1's YONO SMS 118 s away (a
    twin guess); then the mails of both. T2's core-banking mail prints that SMS's balance: it goes there.
    2 rows."""
    make_card("SBI", "4321", name="Savings", active=0)
    _yono_sms(conn, 1092, "600000101", "2026-10-12T04:47:32")
    _cbs_sms(conn, 1093, "61,111.11", "2026-10-12T04:49:30")
    for mail in (_ye("600000101", "10:17:36"), _ye("600000102", "10:19:06"), _ce("61,111.11", "10:19:26")):
        add_email(conn, *mail)
    run(conn)
    _sweep2(conn)
    assert _live(conn) == 2
