"""
One-off post-deploy steps for iteration 203, in dependency order, with a before/after report.

    docker exec ingest python3 scripts/iter203_post_deploy.py --dry-run   # writes nothing
    docker exec ingest python3 scripts/iter203_post_deploy.py             # applies

Run AFTER the new code has started (startup runs the iter-203 migrations and seeds aliases) and after
the owner's private data step (payee rules such as an account alias, subscription cancellations),
which is never committed. Order:
  1. the bank's sample SMS and the test card leave every total; year-late SMS rows re-dated     — E12
  2. 'Sent to people' rows the new categoriser files elsewhere (businesses, payee aliases)       — E4, E10
  3. HDFC "OFFUS EMI,INT" lines are interest, not principal                                     — E6
  4. existing 'unique' merges between two known, different brands                               — E11
  4b. SMS <-> email links guessed far apart against nearer evidence are undone and re-paired     — 198 HP3/H4
  5. own-account EMI transfers become Loan Repayment                                            — E7
  6. statement payment lines and genuine second payments become card payments                  — E8
  6b. payments an older build recorded twice (the bank's two wordings) become one              — E8
  7. like-for-like statement reconcile                                                          — E12
  8. each account's usual gap and last seen (report only)                                       — E13
Every step is idempotent; re-running the script is safe.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import get_db  # noqa: E402

MONTHS = [f"2026-{m:02d}" for m in range(1, 11)]


def snapshot(conn):
    from routes.analytics import analytics_overview
    from routes.cards import cards_summary
    out = {
        "live_rows": conn.execute("SELECT COUNT(*) c FROM transactions WHERE is_duplicate=0").fetchone()["c"],
        "dup_rows": conn.execute("SELECT COUNT(*) c FROM transactions WHERE is_duplicate=1").fetchone()["c"],
        "broken_links": conn.execute(
            "SELECT COUNT(*) c FROM transactions d LEFT JOIN transactions w ON w.id = d.matched_txn_id "
            "WHERE d.is_duplicate = 1 AND d.matched_txn_id IS NOT NULL "
            "AND (w.id IS NULL OR w.id = d.id OR w.is_duplicate = 1)").fetchone()["c"],
        "statements_needs_review": conn.execute(
            "SELECT COUNT(*) c FROM statements WHERE needs_review = 1").fetchone()["c"],
        "cc_payments": conn.execute("SELECT COUNT(*) c FROM cc_payments").fetchone()["c"],
        "payee_rules": _count(conn, "payee_rules"),
        "recurring_cancellations": _count(conn, "recurring_cancellations"),
        "months": {},
        "cards": {},
    }
    for period in MONTHS:
        o = analytics_overview(period=period)
        out["months"][period] = {
            "total": o.get("total"),
            "sent_to_people": o["outflows"]["sent_to_people"]["total"],
            "investments": o["outflows"]["investments"]["total"],
            "loans": o["outflows"]["loans"]["total"],
            "txn_count": o.get("txn_count"),
        }
    for c in cards_summary()["cards"]:
        ls = c.get("latest_statement") or {}
        out["cards"][c["last4"]] = {
            "status": c.get("payment_status"), "due": ls.get("due_date"), "amount_due": ls.get("amount_due"),
            "remaining": c.get("remaining_due"), "unbilled": (c.get("unbilled") or {}).get("total"),
        }
    return out


def _count(conn, table):
    try:
        return conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
    except Exception:                                # table missing = migrations did not run
        return None


def run(dry_run):
    import iter203_be1_steps as be1
    import iter203_be2_steps as be2
    import iter203_f198_steps as f198
    import iter203_fix_steps as fix

    conn = get_db()
    report = {"dry_run": dry_run, "before": snapshot(conn), "steps": []}

    def step(name, fn):
        t = time.time()
        try:
            res = fn()
        except Exception as e:                       # report, never half-hide a failure
            res = {"error": f"{type(e).__name__}: {e}"}
        report["steps"].append({"step": name, "seconds": round(time.time() - t, 1), "result": res})

    step("exclude_test_rows", lambda: be1.step_exclude_test_rows(conn, dry_run=dry_run))
    step("repair_year_late_rows", lambda: be1.step_repair_year_late_rows(conn, dry_run=dry_run))
    step("recategorize_sent_to_people", lambda: be2.step_recategorize_sent_to_people(conn, dry_run=dry_run))
    step("hdfc_emi_interest_kind", lambda: be2.step_hdfc_emi_interest_kind(conn, dry_run=dry_run))
    step("unique_link_review", lambda: be1.step_unique_link_review(conn, dry_run=dry_run))
    step("far_guess_repair", lambda: f198.step_far_guess_repair(conn, dry_run=dry_run))
    step("repair_loan_repayments", lambda: be1.step_repair_loan_repayments(conn, dry_run=dry_run))
    step("card_payments_from_statements", lambda: be1.step_card_payments_from_statements(conn, dry_run=dry_run))
    step("merge_double_recorded_payments", lambda: fix.step_merge_double_recorded_payments(conn, dry_run=dry_run))
    step("reconcile_statements", lambda: be1.step_reconcile_statements(conn, dry_run=dry_run))
    step("silent_accounts", lambda: be1.step_silent_accounts(conn, dry_run=dry_run))

    report["after"] = snapshot(conn)
    return report


if __name__ == "__main__":
    print(json.dumps(run("--dry-run" in sys.argv), indent=1, default=str))
