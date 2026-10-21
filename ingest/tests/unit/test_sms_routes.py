"""iter-18 HP1 / M8: the realtime /sms route, the XML importer and the Gmail SMS-backup path all go through
modules.sms.processing, store phone_timestamp as explicit naive UTC, and date a dateless SMS by its IST
receive day."""
import os
from datetime import datetime, timezone, timedelta

from routes.sms import SmsIngest, _receive_sms_impl, process_pending_sms, sms_quarantine, sms_quarantine_retry
from util.timez import utc_naive_iso_from_epoch

IST = timezone(timedelta(hours=5, minutes=30))


def ms_ist(*args):
    return int(datetime(*args, tzinfo=IST).timestamp() * 1000)


AXIS_NEW = ("Spent INR 979 \nusing Axis Bank Credit Card no. XX9320 on 19-10-26 01:49:17 IST at \nAIRTEL PAYM. \n"
            "Avl limit: INR 157920.62. Not you? SMS BLOCK 9320 to +910000000001.")


def post(sender, body, **kw):
    return _receive_sms_impl(SmsIngest(sender=sender, body=body, **kw))


def test_realtime_axis_new_format_is_recorded(conn, make_card):
    make_card("Axis", "9320", name="Airtel")
    r = post("AD-AXISBK-S", AXIS_NEW, receivedStamp=ms_ist(2026, 10, 19, 1, 49, 30))
    assert r["status"] == "processed"
    assert r["transaction"]["amount"] == 979.0 and r["transaction"]["date"] == "2026-10-19"
    # the forwarder retries: same payload again is a duplicate, not a second spend
    r2 = post("AD-AXISBK-S", AXIS_NEW, receivedStamp=ms_ist(2026, 10, 19, 1, 49, 30))
    assert r2["status"] == "duplicate" and r2["sms_id"] == r["sms_id"]
    assert conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"] == 1


def test_realtime_dateless_sms_no_longer_fails_and_uses_ist(conn, make_card):
    # received 00:30 IST on 19 Oct (= 18 Oct 19:00 UTC). Before iter-18 the realtime insert failed on the
    # NOT NULL date, and the hourly retry dated it 18 Oct from the UTC timestamp.
    make_card("HDFC", "9434", name="Swiggy")
    body = ("Congratulations! Cashback of INR 141 has been credited to your Airtel Axis Bank Credit Card XX9320 "
            "towards your spends during SEP'26.")
    r = post("AD-AXISBK-S", body, receivedStamp=ms_ist(2026, 10, 19, 0, 30, 0))
    assert r["status"] == "processed" and r["transaction"]["date"] == "2026-10-19"
    assert r["transaction"]["kind"] == "cashback"


def test_phone_timestamp_is_explicit_naive_utc(conn):
    ms = ms_ist(2026, 10, 19, 19, 45, 7)
    r = post("AD-HDFCBK-S", "Your OTP is 123456. Do not share.", receivedStamp=ms)
    ts = conn.execute("SELECT phone_timestamp FROM sms WHERE id=?", (r["sms_id"],)).fetchone()[0]
    assert ts == utc_naive_iso_from_epoch(ms) == "2026-10-19T14:15:07"
    # a forwarder that sends the epoch as a string of digits gets the same format
    r = post("AD-HDFCBK-S", "Your OTP is 654321. Do not share.", timestamp=str(ms))
    assert conn.execute("SELECT phone_timestamp FROM sms WHERE id=?", (r["sms_id"],)).fetchone()[0] == "2026-10-19T14:15:07"
    # template literal is still refused (iter-16 D3)
    r = post("AD-HDFCBK-S", "Your OTP is 111111. Do not share.", timestamp="{device_time_millis}")
    assert conn.execute("SELECT phone_timestamp FROM sms WHERE id=?", (r["sms_id"],)).fetchone()[0] is None


def test_realtime_noise_and_quarantine_responses(conn):
    r = post("AD-SWIGGY-S", "Refund of Rs 423.0 has been initiated for Swiggy order 248300000000001. Updated balance "
                            "should reflect in 4-7 days.")
    assert r["is_financial"] and r["status"] == "stored" and r["reason"] == "noise"
    r = post("AD-KOTAKB-S", "Kotak Bank: INR 1,234.00 debited via SuperPay to WIDGET CO on 18-10-26 ref 77")
    assert r["status"] == "quarantined" and r["reason"] == "quarantined"
    q = sms_quarantine()
    assert q["sms_open"] == 1 and q["summary"]["by_bank"] == {"Kotak": 1}
    assert sms_quarantine_retry() == {"retried": 1, "parsed": 0, "resolved": {}, "still_open": 1}
    assert conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"] == 0


def test_process_pending_route(conn):
    conn.execute("INSERT INTO sms (sender, body, phone_timestamp, is_financial) VALUES (?, ?, ?, 1)",
                 ("AX-SBIUPI-S", "Dear UPI user A/C X4321 debited by 645.00 on date 18Oct26 trf to M/S.SOME CINEMA Refno "
                                 "200000000001 If not u? call-1800111109 for other services-18001234-SBI",
                  "2026-10-18T13:00:00"))
    res = process_pending_sms()
    assert res["processed"] == 1 and res["by_class"] == {"transaction": 1}
    t = conn.execute("SELECT amount, card_last4, ref_no, date FROM transactions").fetchone()
    assert tuple(t) == (645.0, "4321", "200000000001", "2026-10-18")
    assert process_pending_sms()["message"] == "No pending SMS"


def _xml(tmp_path, items):
    import xml.etree.ElementTree as ET
    root = ET.Element("smses", count=str(len(items)))
    for sender, body, ms in items:
        ET.SubElement(root, "sms", protocol="0", address=sender, date=str(ms), type="1", body=body, read="1")
    path = tmp_path / "backup.xml"
    ET.ElementTree(root).write(str(path), encoding="utf-8", xml_declaration=True)
    return path


def test_xml_import_timestamps_match_the_realtime_format(conn, tmp_path):
    from modules.sms.xml_import import process_xml_file
    ms = ms_ist(2026, 10, 18, 14, 20, 4)
    body = "Spent Rs.622.22 On HDFC Bank Card 9434 At EDGE CAFE TWO On 2026-10-18:14:20:00.Not You?"
    post("JD-HDFCBK-S", body, receivedStamp=ms)
    res = process_xml_file(str(_xml(tmp_path, [("JD-HDFCBK-S", body, ms)])))
    assert res["duplicates"] == 1 and res["stored"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM sms").fetchone()["c"] == 1


def test_gmail_backup_uses_process_sms_row(conn, tmp_path, monkeypatch):
    # The backup path had its own INSERT loop with the amount+date+description dedup (M4) and swallowed
    # every error. Two genuine identical SBI purchases five minutes apart must both be kept.
    from modules.email import gmail
    monkeypatch.setattr(gmail, "SMS_BACKUP_DIR", str(tmp_path / "backups"))
    body = ("Rs.24.18 spent on your SBI Credit Card ending 2111 at BLINKIT on 18/10/26. Trxn. not done by you? "
            "Report at https://example.invalid/d")
    xml_path = _xml(tmp_path, [("AX-SBICRD-S", body, ms_ist(2026, 10, 18, 15, 11, 0)),
                               ("AX-SBICRD-S", body, ms_ist(2026, 10, 18, 15, 16, 0)),
                               ("AD-SWIGGY-S", "Refund of Rs 423.0 has been initiated for Swiggy order 2483000001. "
                                               "Updated balance should reflect in 4-7 days.", ms_ist(2026, 10, 18, 16, 0, 0))])
    res = gmail._process_sms_backup(xml_path.read_bytes(), "sms-backup.xml")
    assert res["transactions_created"] == 2
    assert os.path.exists(res["file"])
    rows = conn.execute("SELECT amount, card_last4, date FROM transactions").fetchall()
    assert [tuple(r) for r in rows] == [(24.18, "2111", "2026-10-18")] * 2
    assert conn.execute("SELECT COUNT(*) c FROM sms WHERE is_processed = 0").fetchone()["c"] == 0


def test_pluxee_fee_is_financial_and_recorded(conn):
    r = post("JD-Pluxee-S", "Rs. 10.00 deducted from your Pluxee Card xxxx9113 towards INACTIVITY FEE FOR DECEMBER. Pluxee",
             receivedStamp=ms_ist(2026, 1, 24, 13, 45, 0))
    assert r["is_financial"] and r["status"] == "processed"
    t = conn.execute("SELECT amount, kind, card_last4, date FROM transactions").fetchone()
    assert tuple(t) == (10.0, "fee", "9113", "2026-01-24")
