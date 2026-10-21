"""iter-203 E5 — mark a recurring series cancelled.

A subscription the owner cancelled kept showing as active until a charge was missed (~60 days). The
owner can now mark a series cancelled (and undo it): it leaves the active list and the totals, and a
charge after the cancellation date is raised in /health and the daily digest. Merchants made up."""
from datetime import timedelta

import pytest
from fastapi import HTTPException

from modules.digest import build_digest
from routes.system import health_report
from routes.transactions import (
    cancel_recurring, detect_recurring_endpoint, list_recurring_cancellations, uncancel_recurring,
)
from services.queue import init_queue_table
from services.recurring_service import cancelled_series_charged_again
from util.timez import today_ist


def charge(conn, merchant, amount, days_ago, card="1111", source="card"):
    day = (today_ist() - timedelta(days=days_ago)).isoformat()
    conn.execute(
        "INSERT INTO transactions (amount, raw_description, merchant, category, date, source, card_last4, "
        "txn_type, confidence, kind) VALUES (?, ?, ?, 'Subscriptions', ?, ?, ?, 'debit', 'high', 'purchase')",
        (amount, merchant.upper() + " WWW", merchant, day, source, card),
    )
    conn.commit()


def series(res, merchant):
    return [r for r in res["recurring"] if r["merchant"] == merchant]


def test_cancelling_moves_a_series_out_of_active_and_undo_brings_it_back(conn):
    for d in (95, 65, 35, 5):
        charge(conn, "Netflix", 649.0, d)
    charge(conn, "Spotify", 119.0, 10)
    before = detect_recurring_endpoint()
    (netflix,) = series(before, "Netflix")
    assert netflix["status"] == "active" and netflix["series_key"]

    today = today_ist().isoformat()
    out = cancel_recurring({"series_key": netflix["series_key"], "cancelled_on": today, "display_name": "Netflix"})
    assert out["status"] == "cancelled" and out["cancellation"]["cancelled_on"] == today

    after = detect_recurring_endpoint()
    assert series(after, "Netflix") == []                         # not active, not in the totals
    assert len(series(after, "Spotify")) == 1
    (cancelled,) = after["cancelled"]
    assert cancelled["merchant"] == "Netflix" and cancelled["status"] == "cancelled"
    assert cancelled["cancelled_on"] == today and cancelled["next_expected"] is None
    assert cancelled["charged_after_cancel"] == []
    assert after["status_counts"]["cancelled"] == 1
    assert list_recurring_cancellations()["cancellations"][0]["series_key"] == netflix["series_key"]
    # include_ended never puts a cancelled series back into the active list
    assert series(detect_recurring_endpoint(include_ended=True), "Netflix") == []

    res = uncancel_recurring(cancelled["cancellation_id"])
    assert res["status"] == "restored"
    assert len(series(detect_recurring_endpoint(), "Netflix")) == 1
    assert detect_recurring_endpoint()["cancelled"] == []


def test_a_charge_after_cancelling_is_raised_in_health_and_the_digest(conn):
    init_queue_table()
    for d in (70, 40, 12):
        charge(conn, "Netflix", 649.0, d)
    key = series(detect_recurring_endpoint(), "Netflix")[0]["series_key"]
    cancel_recurring({"series_key": key, "cancelled_on": (today_ist() - timedelta(days=10)).isoformat(),
                      "display_name": "Netflix"})
    assert cancelled_series_charged_again(conn) == []

    charge(conn, "Netflix", 649.0, 1)                              # it charged again
    again = cancelled_series_charged_again(conn)
    assert len(again) == 1 and again[0]["merchant"] == "Netflix" and again[0]["charges"][0]["amount"] == 649.0
    (cancelled,) = detect_recurring_endpoint()["cancelled"]
    assert len(cancelled["charged_after_cancel"]) == 1

    health = health_report(conn)
    assert health["correctness"]["cancelled_recurring"] == {"cancelled": 1, "charged_again": 1}
    # /health is public: a count, never the merchant
    (line,) = [w for w in health["warnings"] if "after you cancelled it" in w]
    assert "Netflix" not in line
    digest = build_digest(conn)
    assert "Cancelled but charged again (1)" in digest
    assert "Netflix: ₹649.00" in digest and "after you cancelled it" in digest


def test_cancelling_a_series_that_no_longer_shows_still_lists_it(conn):
    cancel_recurring({"series_key": "sub:acme box", "cancelled_on": "2026-10-01", "display_name": "Acme Box"})
    (row,) = detect_recurring_endpoint()["cancelled"]
    assert row["merchant"] == "Acme Box" and row["detected"] is False


def test_bad_requests_are_refused(conn):
    with pytest.raises(HTTPException):
        cancel_recurring({"cancelled_on": "2026-10-01"})
    with pytest.raises(HTTPException):
        cancel_recurring({"series_key": "sub:x", "cancelled_on": "01/10/2026"})
    with pytest.raises(HTTPException):
        uncancel_recurring(424242)


def test_a_mandate_verification_debit_does_not_hide_the_series(conn):
    charge(conn, "Acme Pro", 2.0, 200)                 # the e-mandate set-up debit
    for d in (95, 65, 35, 5):
        charge(conn, "Acme Pro", 999.0, d)
    (row,) = [r for r in detect_recurring_endpoint()["recurring"] if r["merchant"] == "Acme Pro"]
    assert row["avg_amount"] == 999.0 and row["occurrences"] == 4
    cancel_recurring({"series_key": row["series_key"], "display_name": "Acme Pro"})
    (cancelled,) = detect_recurring_endpoint()["cancelled"]
    assert cancelled["detected"] is True and cancelled["series_key"] == "sub:acme pro"


def test_a_mandate_check_debit_after_cancelling_is_not_a_charge(conn):
    """Verifier round 2: a Rs 1-2 mandate verification debit on a cancelled series raised a false
    'charged again' — the same Rs 10 floor the detection uses now applies."""
    for d in (95, 65, 35):
        charge(conn, "Netflix", 649.0, d)
    (netflix,) = series(detect_recurring_endpoint(), "Netflix")
    cancel_recurring({"series_key": netflix["series_key"], "cancelled_on": (today_ist() - timedelta(days=20)).isoformat()})
    charge(conn, "Netflix", 2.0, 3)                                 # the bank re-checking the mandate
    assert cancelled_series_charged_again(conn) == []
    (cancelled,) = detect_recurring_endpoint()["cancelled"]
    assert cancelled["charged_after_cancel"] == []


def test_a_cancelled_series_shows_its_latest_charge_not_an_old_average(conn):
    """Verifier round 2: a cancelled card read the average of two old cheaper charges and three newer
    ones, not what it last cost (made-up amounts)."""
    for d, amount in ((150, 450.0), (120, 450.0), (90, 900.0), (60, 900.0), (30, 900.0)):
        charge(conn, "Spotify", amount, d)
    (row,) = series(detect_recurring_endpoint(), "Spotify")
    assert row["last_amount"] == 900.0
    cancel_recurring({"series_key": row["series_key"]})
    (cancelled,) = detect_recurring_endpoint()["cancelled"]
    assert cancelled["last_amount"] == 900.0
