"""
Iteration 203, lane BE-1 (cards + data): the history repairs behind E7, E8, E11, E12 and E13.

Every step is `step_<name>(conn, dry_run=True) -> dict`, generic (no owner data), idempotent, and
writes nothing when dry_run is true. The iteration-203 post-deploy runner calls them in order:

  step_repair_loan_repayments     E7   own-account EMI transfers -> Loan Repayment (pairing.pair_rows)
  step_unique_link_review         E11  existing 'unique' merges the new merchant check would refuse
  step_repair_year_late_rows      E12  SMS rows dated far from their SMS receive date (year re-inferred)
  step_exclude_test_rows          E12  the bank's sample SMS (a/c XX1234 ... Avl bal Rs.12,345.67) + test card
  step_card_payments_from_statements  E8   statement "PAYMENT RECEIVED" lines -> card payments (de-duplicated)
  step_reconcile_statements       E12  re-run the like-for-like statement reconcile
  step_silent_accounts            E13  report each account's usual gap and last seen (read-only)
"""
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

YEAR_LATE_DAYS = 45
# The bank's own sample message ("Rs.250.00 debited from a/c XX1234 ... Avl bal Rs.12,345.67"): it
# arrived in March 2026 and was counted as money sent to a person.
_SAMPLE_SMS = re.compile(r"\bXX1234\b.*?\b12,?345\.67\b", re.I | re.S)


def step_repair_year_late_rows(conn, dry_run=True):
    """E12: an SMS row dated more than 45 days from the IST day its SMS arrived got its year from an
    old parser that assumed the current year for "On 16-02" (three Feb/Mar-2025 card spends sat in
    2026). The year is re-inferred from the SMS's own arrival day (util.timez.infer_year); a repaired
    row is then offered to the merge rules, since its statement copy now falls inside the window."""
    from modules import merge
    from util.timez import infer_year, ist_date_of
    rows = []
    for r in conn.execute(
            "SELECT t.id, t.date, t.occurred_at, s.phone_timestamp, s.received_at FROM transactions t "
            "JOIN sms s ON s.id = CAST(t.source_ref AS INTEGER) WHERE t.source = 'sms'").fetchall():
        arrived = ist_date_of(r["phone_timestamp"] or r["received_at"])
        try:
            on, got = date.fromisoformat(r["date"][:10]), date.fromisoformat(arrived)
        except (TypeError, ValueError):
            continue
        if abs((on - got).days) <= YEAR_LATE_DAYS:
            continue
        try:
            fixed = date(infer_year(on.month, on.day, got), on.month, on.day)
        except ValueError:
            continue
        if abs((fixed - got).days) > YEAR_LATE_DAYS or fixed == on:
            continue
        rows.append({"id": r["id"], "from": on.isoformat(), "to": fixed.isoformat(), "sms_arrived": got.isoformat()})
        if not dry_run:
            occurred = r["occurred_at"]
            if occurred and occurred.startswith(on.isoformat()):
                occurred = fixed.isoformat() + occurred[10:]
            conn.execute("UPDATE transactions SET date = ?, occurred_at = ? WHERE id = ?",
                         (fixed.isoformat(), occurred, r["id"]))
    if not dry_run:
        conn.commit()
        for row in rows:
            row["linked"] = merge.link_on_insert(conn, row["id"])
        conn.commit()
    return {"dry_run": dry_run, "rows": rows}


def step_exclude_test_rows(conn, dry_run=True):
    """E12: the bank's sample SMS is not a transaction. Its rows are flagged (is_duplicate = 1,
    merge_rule 'test-message', no winner) — never deleted — so every total ignores them; a card
    that exists only because of such a row (no statements, no payments, no other transaction) is
    removed and the cards config re-exported, so a restart cannot bring it back."""
    import db as db_module
    rows = [r for r in conn.execute(
        "SELECT id, card_last4, raw_text FROM transactions WHERE is_duplicate = 0 AND raw_text IS NOT NULL")
        if _SAMPLE_SMS.search(r["raw_text"] or "")]
    ids = [r["id"] for r in rows]
    cards = []
    for last4 in sorted({r["card_last4"] for r in rows if r["card_last4"]}):
        card = conn.execute("SELECT id FROM cards WHERE last4 = ?", (last4,)).fetchone()
        if not card:
            continue
        others = conn.execute(
            f"SELECT COUNT(*) c FROM transactions WHERE card_last4 = ? AND id NOT IN ({','.join('?' * len(ids))}) "
            "AND NOT (is_duplicate = 1 AND merge_rule = 'test-message')", (last4, *ids)).fetchone()["c"]
        stmts = conn.execute("SELECT COUNT(*) c FROM statements WHERE card_id = ?", (card["id"],)).fetchone()["c"]
        pays = conn.execute("SELECT COUNT(*) c FROM cc_payments WHERE card_last4 = ?", (last4,)).fetchone()["c"]
        if others == 0 and stmts == 0 and pays == 0:
            cards.append(last4)
    if not dry_run:
        conn.executemany("UPDATE transactions SET is_duplicate = 1, matched_txn_id = NULL, merge_rule = 'test-message' "
                         "WHERE id = ?", [(i,) for i in ids])
        for last4 in cards:
            # the flagged rows keep their card_last4; only the link to the card row goes
            conn.execute("UPDATE transactions SET card_id = NULL WHERE card_id IN (SELECT id FROM cards WHERE last4 = ?)",
                         (last4,))
            conn.execute("DELETE FROM cards WHERE last4 = ?", (last4,))
        conn.commit()
        if cards:
            db_module.export_cards_config(conn)
    return {"dry_run": dry_run, "rows": ids, "cards": cards}


def step_silent_accounts(conn, dry_run=True):
    """E13 (read-only): each account's usual gap and last transaction, as /health and the digest
    judge them today."""
    from routes.system import silent_accounts
    rows = silent_accounts(conn)
    return {"dry_run": dry_run, "accounts": rows,
            "silent": [f"{r['bank']} {r['last4']}" for r in rows if r["state"] == "silent"]}


def step_reconcile_statements(conn, dry_run=True):
    """E12: re-run the like-for-like reconcile (after the statement pass stored each PDF's printed
    summary). Dry run reports the current flags only."""
    from services.cards_service import reconcile_statements_logic
    before = conn.execute("SELECT COUNT(*) c FROM statements WHERE needs_review = 1").fetchone()["c"]
    if dry_run:
        return {"dry_run": True, "needs_review_now": before}
    res = reconcile_statements_logic(conn=conn)
    flagged = [dict(r) for r in conn.execute(
        "SELECT c.last4, s.billing_end, s.stated_total, s.computed_total, s.reconcile_method, s.reconcile_gap "
        "FROM statements s JOIN cards c ON c.id = s.card_id WHERE s.needs_review = 1 ORDER BY c.last4, s.billing_end")]
    return {"dry_run": False, "needs_review_before": before, **res, "flagged": flagged}


def _loans_by_month(year=2026, last_month=10):
    from routes.analytics import analytics_overview
    out = {}
    for m in range(1, last_month + 1):
        o = analytics_overview(period=f"{year}-{m:02d}")
        out[f"{year}-{m:02d}"] = o["outflows"]["loans"]
    return out


def step_card_payments_from_statements(conn, dry_run=True, directory=None):
    """E8: (1) payment SMS the new identity rules keep apart from the payment they had been folded
    into (genuine second same-amount payments) are recorded; (2) every statement PDF's payment lines
    become payments, de-duplicated against SMS / email / CRED / bank-debit reports."""
    from services import cards_service as cs
    from modules.pdf.store import payments_from_statements
    sms_to_add = cs.sms_payments_not_recorded(conn)
    if dry_run:
        return {"dry_run": True, "sms_payments_to_add": sms_to_add,
                "statement": payments_from_statements(conn, dry_run=True, directory=directory)}
    sms = cs.backfill_payments_logic(conn)
    stmt = payments_from_statements(conn, dry_run=False, directory=directory)
    return {"dry_run": False, "sms_payments_added": sms_to_add, "sms_backfill": sms, "statement": stmt}


def step_unique_link_review(conn, dry_run=True):
    """E11: links the old `unique` rule made between two known, different brands with nothing tying
    them together. Dry run lists them; the real run makes each copy a live row again."""
    from modules.merge import unique_links_refused
    res = unique_links_refused(conn, unlink=not dry_run)
    res["dry_run"] = dry_run
    res["count"] = len(res["refused"])
    res["amount"] = round(sum(r["amount"] for r in res["refused"]), 2)
    return res


def step_repair_loan_repayments(conn, dry_run=True):
    """E7: EMIs SBI words as 'Transferred to Mr. <owner>' become Loan Repayment where the loan
    account's credit SMS, a named loan account or an already-proven EMI amount says so."""
    from modules.pairing import pair_rows
    before = _loans_by_month()
    res = pair_rows(conn, dry_run=dry_run)
    out = {"dry_run": dry_run, "pairing": res, "loans_by_month_before": before}
    if not dry_run:
        out["loans_by_month_after"] = _loans_by_month()
    return out
