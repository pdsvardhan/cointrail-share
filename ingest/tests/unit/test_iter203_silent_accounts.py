"""Silent accounts and a missing SMS backup are warnings — iteration 203 E13.

HSBC accounts arrive only by SMS (no alert emails, no statements), so a forwarder that stops loses
them for good. /health now warns when an account that normally reports goes quiet for longer than
its own usual gap. Account numbers and amounts here are made up."""
from datetime import timedelta

import pytest

import config
from modules import digest
from routes import cards as cards_routes
from routes import system
from services.queue import init_queue_table
from util.timez import today_ist


@pytest.fixture
def hconn(conn, monkeypatch, tmp_path):
    init_queue_table()
    marker = tmp_path / ".last-imported"
    marker.write_text("sms-20261020060000.xml\n")                 # a backup imported just now
    monkeypatch.setattr(config, "SMS_BACKUP_MARKER", str(marker))
    return conn


def history(conn, last4, days_ago):
    for d in days_ago:
        on = (today_ist() - timedelta(days=d)).isoformat()
        conn.execute("INSERT INTO transactions (amount, raw_description, date, source, source_ref, card_last4, "
                     "txn_type, is_duplicate) VALUES (100, 'per your request', ?, 'sms', ?, ?, 'debit', 0)",
                     (on, f"{last4}-{d}", last4))
    conn.commit()


def silent_lines(r):
    return [w for w in r["warnings"] if "quiet" in w]


def test_a_regular_account_silent_past_its_usual_gap_warns(hconn, make_card):
    make_card("HSBC", "001", name="Account 001", active=0)
    history(hconn, "001", range(12, 130, 2))                        # every 2 days, then 12 days of silence
    history(hconn, "7777", [0])                                      # everything else keeps arriving
    r = system.health_report(hconn)
    block = r["correctness"]["silent_accounts"]
    assert block["silent"] == 1 and block["by_bank"] == {"HSBC": 1}
    (line,) = silent_lines(r)
    assert "HSBC" in line and "001" not in line                     # /health is public: no account numbers
    assert r["status"] == "ok"                                      # a warning, not an outage


def test_an_account_within_its_usual_gap_does_not_warn(hconn, make_card):
    make_card("HSBC", "001", name="Account 001", active=0)
    history(hconn, "001", range(3, 130, 2))
    r = system.health_report(hconn)
    assert r["correctness"]["silent_accounts"]["silent"] == 0 and silent_lines(r) == []


def test_sparse_dormant_and_replaced_accounts_are_skipped(hconn, make_card):
    make_card("HSBC", "001", name="Account 001", active=0)
    history(hconn, "001", (20, 90, 150))                              # sparse: no usual gap to speak of
    make_card("HDFC", "4400", name="Old card", statement_day=22, due_day=12)
    history(hconn, "4400", range(70, 170, 3))                         # quiet for 70 days: dormant, not an alarm
    new = make_card("HDFC", "4500", name="New card", statement_day=22, due_day=12)
    make_card("HDFC", "4300", name="Reissued", statement_day=22, due_day=12)
    hconn.execute("UPDATE cards SET replaced_by_card_id = ? WHERE last4 = '4300'", (new,))
    history(hconn, "4300", range(20, 120, 2))
    block = system.health_report(hconn)["correctness"]["silent_accounts"]
    assert block["silent"] == 0 and block["dormant"] == 1


def test_the_digest_names_the_silent_account(hconn, make_card, monkeypatch):
    monkeypatch.setattr(cards_routes, "cards_summary", lambda: {"cards": []})
    make_card("HSBC", "001", name="Account 001", active=0)
    history(hconn, "001", range(12, 130, 2))
    text = digest.build_digest(hconn)
    assert "Silent accounts" in text and "HSBC Account 001 ••001" in text


def test_a_missing_sms_backup_marker_is_a_warning(hconn, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SMS_BACKUP_MARKER", str(tmp_path / "never-written"))
    r = system.health_report(hconn)
    assert r["correctness"]["sms_backup"]["configured"] is False
    (line,) = [w for w in r["warnings"] if "backup" in w]
    assert "not set up" in line
