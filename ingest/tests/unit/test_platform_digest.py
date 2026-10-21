"""iter-18 HP6: the daily digest (modules/digest.py) and its routes.

Card dues come from /cards/summary (contract 3, owned by the cards workstream); here it is
replaced by contract-shaped data so the digest's own selection and wording are what is tested."""
from datetime import timedelta

import pytest

import config
from middleware.security import PUBLIC_PATHS
from modules import digest, notify, quarantine
from routes import cards as cards_routes
from routes import system
from services.queue import init_queue_table
from util.timez import today_ist

AXIS_NEW_SPEND = ("Spent INR 979 \nusing Axis Bank Credit Card no. XX9320 on 19-10-26 01:49:17 IST at \n"
                  "AIRTEL PAYM. \nAvl limit: INR 157920.62. Not you? SMS BLOCK 9320 to +919900000000.")


def _d(days):
    return (today_ist() + timedelta(days=days)).isoformat()


def card(bank, last4, name, status, due=None, source="bank", amount_due=None, remaining=None, closed=None):
    return {
        "bank": bank, "last4": last4, "card_name": name, "payment_status": status,
        "is_paid": status in ("paid", "no_dues"), "remaining_due": remaining,
        "latest_statement": {"due_date": due, "due_date_source": source, "amount_due": amount_due,
                             "billing_end": closed},
    }


@pytest.fixture
def dconn(conn, monkeypatch, tmp_path):
    init_queue_table()
    marker = tmp_path / "fresh-backup-marker"                      # a missing marker is a warning (203 E13)
    marker.write_text("sms-20261020060000.xml\n")
    monkeypatch.setattr(config, "SMS_BACKUP_MARKER", str(marker))
    return conn


@pytest.fixture
def summary(monkeypatch):
    holder = {"cards": []}
    monkeypatch.setattr(cards_routes, "cards_summary", lambda: {"cards": holder["cards"]})
    return holder


def test_dues_use_the_bank_date_and_put_overdue_first(dconn, summary):
    summary["cards"] = [
        card("Axis", "9320", "Airtel", "unpaid", due=_d(12), source="estimated", amount_due=1051.38, remaining=1051.38),
        card("HDFC", "4956", "Tata Neu", "unpaid", due=_d(1), amount_due=13836.54, remaining=13836.54),
        card("HDFC", "9434", "Swiggy", "paid", due=_d(-5), amount_due=4599, remaining=0),
        card("ICICI", "0026", "Amazon Pay", "overdue", due=_d(-2), amount_due=108767, remaining=8767),
        card("SBI", "2111", "Cashback", "partial", due=_d(3), amount_due=46088, remaining=1088),
        card("SBI", "2971", "AURUM", "not_confirmed", due=_d(-60), amount_due=179.68, closed=_d(-80)),
        card("HSBC", "8938", "Platinum", "no_dues"),
    ]
    text = digest.build_digest(dconn)
    dues = text.split("Card dues\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert dues[0].startswith("• OVERDUE ICICI Amazon Pay ••0026: ₹8,767.00 was due")
    assert "(2 days ago, bank date)" in dues[0]
    assert dues[1].startswith("• HDFC Tata Neu ••4956: ₹13,836.54 due") and "(tomorrow, bank date)" in dues[1]
    assert dues[2].startswith("• SBI Cashback ••2111: ₹1,088.00 due") and dues[2].endswith(", part paid")
    assert dues[3].startswith("• Axis Airtel ••9320: ₹1,051.38 due") and "(in 12 days, estimated)" in dues[3]
    assert dues[4].startswith("• SBI AURUM ••2971: no payment seen") and "check the bank app" in dues[4]
    assert len(dues) == 5                                               # paid / no_dues are omitted
    assert "9434" not in text and "8938" not in text


def test_nothing_owed_and_clean_checks(dconn, summary):
    summary["cards"] = [card("HDFC", "9434", "Swiggy", "paid", due=_d(-5))]
    text = digest.build_digest(dconn)
    assert text.startswith(f"Cointrail daily digest · {today_ist():%a %d %b %Y}")
    assert "• Nothing owed right now." in text
    assert "All checks clean" in text and "Warnings" not in text and "Quarantine" not in text


def test_missing_contract_fields_are_said_out_loud(dconn, summary):
    summary["cards"] = [{"bank": "HDFC", "last4": "4956", "is_paid": False, "latest_statement": {}}]
    assert "cards contract 3 is not deployed" in digest.build_digest(dconn)


def test_quarantine_review_and_warnings_sections(dconn, summary, make_card):
    for ref, amount in (("21", "979"), ("22", "49")):
        quarantine.quarantine(dconn, "sms", ref, "AD-AXISBK-S", AXIS_NEW_SPEND.replace("979", amount), "no_pattern")
    icici = make_card("ICICI", "0026", name="Amazon Pay", statement_day=21, due_day=8)
    dconn.execute(
        "INSERT INTO statements (card_id, month, year, billing_start, billing_end, stated_total, "
        "computed_total, needs_review) VALUES (?, 9, 2026, ?, ?, 108767, 23885, 1)",
        (icici, _d(-60), _d(-30)))
    dconn.execute("INSERT INTO sms_receipts (received_at, status) VALUES (datetime('now', '-20 hours'), 'stored')")

    text = digest.build_digest(dconn)
    assert "Warnings\n• 2 bank message(s) in quarantine" in text
    assert "No SMS has reached Ingest from the phone forwarder for" in text
    quarantine_part = text.split("Quarantine — 2 bank message(s) no rule could read\n", 1)[1]
    assert quarantine_part.startswith("• Axis ×2: Spent INR # using Axis Bank Credit Card no. XX#")
    assert "9320" not in quarantine_part.split("\n\n")[0]                 # templates mask digits
    assert "• ICICI Amazon Pay ••0026 closed" in text
    assert "bank ₹1,08,767.00 vs parsed ₹23,885.00" in text
    assert "All checks clean" not in text


def test_health_issues_lead_the_digest(dconn, summary):
    dconn.execute("INSERT INTO task_queue (task_type, status, completed_at) "
                  "VALUES ('email_sync', 'failed', datetime('now'))")
    text = digest.build_digest(dconn)
    assert "\n\nHealth: degraded\n• task failures (24h): email_syncx1" in text


def test_send_daily_digest_delivers_and_keeps_text_out_of_the_task_result(dconn, summary, monkeypatch):
    got = []
    monkeypatch.setattr(notify, "send", lambda title, text: got.append((title, text)) or True)
    monkeypatch.setattr(notify, "configured_channels", lambda settings=None: ["telegram"])
    result = digest.send_daily_digest(dconn)
    assert result["sent"] is True and result["channels"] == ["telegram"] and "text" not in result
    (title, text), = got
    assert title == f"Cointrail digest {today_ist():%d %b}" and text.startswith("Cointrail daily digest")
    assert digest.send_daily_digest(dconn, include_text=True)["text"] == text


def test_digest_routes(dconn, summary, monkeypatch):
    monkeypatch.setattr(notify, "send", lambda title, text: False)
    r = system.get_digest()
    assert r["text"].startswith("Cointrail daily digest") and r["generated_at"].endswith("+05:30")
    sent = system.send_digest()
    assert sent["sent"] is False and sent["text"].startswith("Cointrail daily digest")
    assert "/digest" not in PUBLIC_PATHS and "/digest/send" not in PUBLIC_PATHS


@pytest.mark.parametrize("amount,expected", [
    (108767, "₹1,08,767.00"), (13836.54, "₹13,836.54"), (511, "₹511.00"),
    (12345678.9, "₹1,23,45,678.90"), (-199, "-₹199.00"), (None, "amount unknown"),
])
def test_indian_amount_format(amount, expected):
    assert digest._inr(amount) == expected
