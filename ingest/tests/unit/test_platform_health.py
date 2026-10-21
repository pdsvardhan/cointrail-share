"""iter-18 HP6 / M16: /health reports correctness, not only freshness.

Before iter-18 /health stayed 'ok' while 6 of 7 October Axis spends were dropped (a new wording),
two cards showed a due date later than the bank's, and the SMS backup had been dead for months.
Every test below would have failed against the old /health: it had no `correctness` or `warnings`.
Message bodies are real wordings from the 2026 corpus (people's names and account digits altered).
"""
import os
import time
from datetime import timedelta

import pytest

import config
from modules import quarantine
from routes import system
from services.queue import init_queue_table
from util.timez import today_ist

AXIS_NEW_SPEND = ("Spent INR 979 \nusing Axis Bank Credit Card no. XX9320 on 19-10-26 01:49:17 IST at \n"
                  "AIRTEL PAYM. \nAvl limit: INR 157920.62. Not you? SMS BLOCK 9320 to +919900000000.")
ICICI_REVERSAL = ("Reversal of Rs 4.01 credited to ICICI Bank Credit Card XX0026 on 03-APR-26. "
                  "Revised total due Rs 0, minimum due Rs .00")
HSBC_DEBIT = ("HSBC: Dear Customer, your HSBC A/c 111-000***-001 has been debited with INR 12.34- on 13OCT "
              "as per your request . Your avl Bal is 100,000.00 .")
AXIS_DUE_REMINDER = ("Payment of INR 2411.32 for Axis Bank Credit Card no. XX9320 is due on 01-08-26 with "
                     "minimum amount due of INR 100. Ignore if paid.")
AXIS_OTP = ("309535 is SECRET OTP for txn of INR 979.00 on Axis Bank card XX9320 at AIRTEL PAY on "
            "19-10-26 01:48:52. OTP valid for 5 mins. Please do not share this OTP.")
SBI_UPI = ("Dear UPI user A/C X4321 debited by 645.00 on date 18Oct26 trf to M/S.APARNA CINEM "
           "Refno 200000000009 If not u? call-1800111109 for other services-18001234-SBI")
AIRTEL_FAILED = ("Hi, unfortunately, your payment of Rs. 39 with Transaction ID: 6000000000000000002 for "
                 "your PREPAID 9000000000 was unsuccessful. If any amount was debited, it will be refunded "
                 "within 7-10 days.")


@pytest.fixture
def hconn(conn, monkeypatch, tmp_path):
    init_queue_table()                                            # /health reads task_queue
    # a backup imported just now; a missing marker is itself a warning (iteration 203 E13)
    marker = tmp_path / "fresh-backup-marker"
    marker.write_text("sms-20261020060000.xml\n")
    monkeypatch.setattr(config, "SMS_BACKUP_MARKER", str(marker))
    return conn


def add_sms(conn, sender, body, processed=1, age="-1 hours"):
    cur = conn.execute(
        "INSERT INTO sms (sender, body, is_financial, is_processed, received_at) "
        "VALUES (?, ?, 1, ?, datetime('now', ?))",
        (sender, body, processed, age),
    )
    return cur.lastrowid


def warning_for(report, needle):
    return [w for w in report["warnings"] if needle in w]


def test_empty_db_is_ok_with_a_clean_correctness_block(hconn):
    r = system.health_report(hconn)
    assert r["status"] == "ok" and r["issues"] == [] and r["warnings"] == []
    c = r["correctness"]
    assert c["quarantine"] == {"open": 0, "by_bank": {}, "oldest_hours": None}
    assert c["unaccounted_sms"]["count"] == 0
    assert c["statements_needs_review"]["recent"] == 0
    assert c["due_date_estimated"]["estimated"] == 0
    assert c["sms_receipts"]["hours_since_last"] is None
    assert c["sms_backup"]["configured"] is True and c["sms_backup"]["imported_hours"] < 1


def test_open_quarantine_is_a_warning_not_an_issue(hconn):
    quarantine.quarantine(hconn, "sms", "11", "AD-AXISBK-S", AXIS_NEW_SPEND, "no_pattern")
    quarantine.quarantine(hconn, "sms", "12", "AD-AXISBK-S", AXIS_NEW_SPEND.replace("979", "49"), "no_pattern")
    quarantine.quarantine(hconn, "sms", "13", "JM-HSBCIN-S", HSBC_DEBIT, "no_pattern")
    hconn.execute("UPDATE parse_quarantine SET first_seen = datetime('now', '-30 hours') WHERE source_ref = '13'")
    quarantine.quarantine(hconn, "sms", "14", "AX-ICICIT-S", ICICI_REVERSAL, "no_pattern")
    quarantine.resolve(hconn, "sms", "14", "pattern_added", transaction_id=1)

    r = system.health_report(hconn)
    q = r["correctness"]["quarantine"]
    assert q["open"] == 3 and q["by_bank"] == {"Axis": 2, "HSBC": 1}
    assert 29.9 <= q["oldest_hours"] <= 30.1
    assert r["status"] == "ok" and r["issues"] == []          # the 6-hourly cron must not page
    (line,) = warning_for(r, "quarantine")
    assert "3 bank message(s)" in line and "Axis 2, HSBC 1" in line


def test_silently_dropped_bank_sms_are_counted(hconn):
    dropped_axis = add_sms(hconn, "AD-AXISBK-S", AXIS_NEW_SPEND)          # the H6 drop
    add_sms(hconn, "AX-ICICIT-S", ICICI_REVERSAL)                        # a refund, not a statement
    recorded = add_sms(hconn, "VK-AXISBK-S", AXIS_NEW_SPEND.replace("979", "399"))
    hconn.execute("INSERT INTO transactions (amount, date, source, source_ref) VALUES (399, '2026-10-06', 'sms', ?)",
                  (str(recorded),))
    held = add_sms(hconn, "JM-HSBCIN-S", HSBC_DEBIT)
    quarantine.quarantine(hconn, "sms", held, "JM-HSBCIN-S", HSBC_DEBIT, "no_pattern")
    add_sms(hconn, "AX-AXISBK-S", AXIS_DUE_REMINDER)                     # known noise
    add_sms(hconn, "JM-AXISBK-T", AXIS_OTP)                              # an OTP, never a spend
    add_sms(hconn, "AX-SBIUPI-S", SBI_UPI, age="-8 days")                # outside the 7-day window
    add_sms(hconn, "AD-AIRINF", AIRTEL_FAILED)                           # a merchant, not a bank
    add_sms(hconn, "AD-AXISBK-S", AXIS_NEW_SPEND.replace("979", "49"), processed=0)  # still pending

    r = system.health_report(hconn)
    u = r["correctness"]["unaccounted_sms"]
    assert u["count"] == 2 and u["by_bank"] == {"Axis": 1, "ICICI": 1}
    (line,) = warning_for(r, "dropped silently")
    assert line.startswith("2 transaction-looking bank SMS")

    quarantine.quarantine(hconn, "sms", dropped_axis, "AD-AXISBK-S", AXIS_NEW_SPEND, "no_pattern")
    assert system.health_report(hconn)["correctness"]["unaccounted_sms"]["by_bank"] == {"ICICI": 1}


def test_card_bill_payments_are_not_drops(hconn):
    # Both say "credited" with an amount, so they look like money movement; both are bill
    # payments that cards_service files in cc_payments, never transactions.
    add_sms(hconn, "JM-HDFCBK-S", "HDFC Bank Cardmember, Online Payment of Rs.4599 vide Ref# 000000000CukpFI "
                                  "was credited to your card ending 9434 On 07/OCT/2026_value Date 07/OCT/2026")
    add_sms(hconn, "VM-SBICRD-S", "We have received payment of Rs.4,599.00 via BBPS & the same has been "
                                  "credited to your SBI Credit Card. Your available limit is Rs.12,345.00.")
    assert system.health_report(hconn)["correctness"]["unaccounted_sms"]["count"] == 0


def _statement(conn, card_id, month, billing_end, needs_review=0, due_date=None, source=None):
    conn.execute(
        "INSERT INTO statements (card_id, month, year, billing_start, billing_end, needs_review, "
        "due_date, due_date_source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (card_id, month, int(billing_end[:4]), billing_end, billing_end, needs_review, due_date, source),
    )


def test_only_recent_statement_mismatches_warn(hconn, make_card):
    card = make_card("ICICI", "0026", name="Amazon Pay", statement_day=21, due_day=8)
    recent = (today_ist() - timedelta(days=29)).isoformat()
    old = (today_ist() - timedelta(days=400)).isoformat()
    _statement(hconn, card, 1, recent, needs_review=1)
    _statement(hconn, card, 2, old, needs_review=1)
    r = system.health_report(hconn)
    assert r["correctness"]["statements_needs_review"] == {"recent": 1, "total": 2, "recent_days": 90}
    (line,) = warning_for(r, "need review")
    assert line.startswith("1 statement(s)") and "(2 flagged in all)" in line


def test_current_cycle_cards_without_a_bank_due_date_warn(hconn, make_card):
    axis = make_card("Axis", "9320", name="Airtel", statement_day=12, due_day=5)
    hdfc = make_card("HDFC", "4956", name="Tata Neu", statement_day=1, due_day=21)
    dormant = make_card("HSBC", "8938", name="Platinum", statement_day=4, due_day=24)
    gone = make_card("HDFC", "7180", name="Millennia", active=0)
    fresh = (today_ist() - timedelta(days=6)).isoformat()
    _statement(hconn, axis, 10, fresh)                                         # estimated: warn
    _statement(hconn, hdfc, 10, fresh, due_date="2026-10-21", source="bank")    # bank date: fine
    _statement(hconn, dormant, 3, (today_ist() - timedelta(days=200)).isoformat())
    _statement(hconn, gone, 10, fresh)
    # An older statement must not hide the latest one's missing bank date.
    _statement(hconn, axis, 9, (today_ist() - timedelta(days=37)).isoformat(), due_date="2026-10-02", source="bank")

    r = system.health_report(hconn)
    assert r["correctness"]["due_date_estimated"] == {"estimated": 1, "current_cycle_cards": 2,
                                                      "by_bank": {"Axis": 1}}
    (line,) = warning_for(r, "estimated due date")
    assert line.startswith("1 of 2 current-cycle card(s)") and "Axis 1" in line
    assert "9320" not in " ".join(r["warnings"])                  # /health is public: no card numbers


def test_silent_forwarder_warns_after_12_hours(hconn):
    hconn.execute("INSERT INTO sms_receipts (received_at, status) VALUES (datetime('now', '-11 hours'), 'stored')")
    assert warning_for(system.health_report(hconn), "forwarder") == []
    hconn.execute("DELETE FROM sms_receipts")
    hconn.execute("INSERT INTO sms_receipts (received_at, status) VALUES (datetime('now', '-14 hours'), 'stored')")
    r = system.health_report(hconn)
    assert 13.9 <= r["correctness"]["sms_receipts"]["hours_since_last"] <= 14.1
    (line,) = warning_for(r, "forwarder")
    assert "limit 12h" in line
    assert r["status"] == "ok"


def test_stale_sms_backup_marker_warns(hconn, monkeypatch, tmp_path):
    marker = tmp_path / ".last-imported"
    marker.write_text("sms-20260812060019.xml\n")
    old = time.time() - 40 * 3600
    os.utime(marker, (old, old))
    monkeypatch.setattr(config, "SMS_BACKUP_MARKER", str(marker))
    r = system.health_report(hconn)
    b = r["correctness"]["sms_backup"]
    assert b["configured"] is True and b["last_backup"] == "sms-20260812060019.xml"
    assert 39.9 <= b["imported_hours"] <= 40.1
    assert b["backup_age_hours"] > 24 * 60                       # taken 12 Aug, read in Oct or later
    (line,) = warning_for(r, "backup")
    assert "limit 36h" in line and "only capture path" in line

    os.utime(marker, None)                                       # a backup imported just now
    assert warning_for(system.health_report(hconn), "backup") == []


def test_a_broken_check_degrades_to_a_warning_not_a_500(hconn):
    hconn.execute("DROP TABLE parse_quarantine")
    r = system.health_report(hconn)
    assert "error" in r["correctness"]["quarantine"]
    assert warning_for(r, "Correctness check 'quarantine' failed")
    assert r["correctness"]["statements_needs_review"]["total"] == 0     # the others still ran


def test_daily_digest_task_is_not_held_to_the_hourly_limit(hconn):
    for task, hours_ago in (("daily_digest", 20), ("email_sync", 5)):
        hconn.execute("INSERT INTO task_queue (task_type, status, completed_at) "
                      "VALUES (?, 'completed', datetime('now', ?))", (task, f"-{hours_ago} hours"))
    r = system.health_report(hconn)
    assert r["issues"] == ["email_sync has not succeeded in 5.0h"]
    hconn.execute("UPDATE task_queue SET completed_at = datetime('now', '-27 hours') WHERE task_type = 'daily_digest'")
    assert "daily_digest has not succeeded in 27.0h" in system.health_report(hconn)["issues"]


def test_health_route_returns_the_report(hconn):
    r = system.health()
    assert {"status", "issues", "correctness", "warnings"} <= set(r)
