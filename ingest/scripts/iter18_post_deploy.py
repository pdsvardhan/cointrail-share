"""
One-off post-deploy steps for iteration 198 (iter-18), in dependency order, with a before/after report.

    docker exec ingest python3 scripts/iter18_post_deploy.py --dry-run   # writes nothing
    docker exec ingest python3 scripts/iter18_post_deploy.py             # applies

Order matters:
  1. retire merchant refund notices (they were never bank credits)          — SMS H5a
  2. relabel statement payment lines stored as purchases                     — PDF M2
  3. recategorise history with source context, THEN backfill kinds           — M14, HP4
  4. reprocess SMS that never became transactions (new patterns, refunds)   — HP1, H5a, H6, M5/M6
  5. re-parse statement PDFs with the fixed parsers (missing lines only)     — M3, H8
  6. bank alert emails since 2025-10 become transactions (linked on insert)  — H4
  7. store the reference every legacy SMS prints, undo old links whose group holds two
     transactions, then the merge sweep over everything
     (SMS+email+statement copies -> one row)                                 — H3, HP3, M4
  7b. pair own-account transfers, card autopays and reversals                 — H5b
  7c. re-make every email->transaction link under today's enrichment rules     — M12
  8. card maintenance: statements, payments (SMS/email/CRED/bank debits),
     bank due dates, reconcile, BBPS attribution, card ids                   — H1, H2, M1, HP5
  9. retry the quarantine, backfill any kind still NULL
Every step is idempotent; re-running the script is safe.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db  # noqa: E402


def snapshot(conn):
    from routes.analytics import analytics_overview
    from routes.cards import cards_summary
    from modules.quarantine import summary as qsummary
    out = {
        "live_rows": conn.execute("SELECT COUNT(*) c FROM transactions WHERE is_duplicate=0").fetchone()["c"],
        "dup_rows": conn.execute("SELECT COUNT(*) c FROM transactions WHERE is_duplicate=1").fetchone()["c"],
        "kind_null": conn.execute("SELECT COUNT(*) c FROM transactions WHERE kind IS NULL").fetchone()["c"],
        "quarantine_open": qsummary(conn)["open"],
        # every duplicate must point at a live row: a self-link or a chain into another duplicate
        # would make a real purchase disappear from every total
        "broken_links": conn.execute(
            "SELECT COUNT(*) c FROM transactions d LEFT JOIN transactions w ON w.id = d.matched_txn_id "
            "WHERE d.is_duplicate = 1 AND d.matched_txn_id IS NOT NULL "
            "AND (w.id IS NULL OR w.id = d.id OR w.is_duplicate = 1)").fetchone()["c"],
        "merge_rules": {r["merge_rule"] or "legacy": r["c"] for r in conn.execute(
            "SELECT merge_rule, COUNT(*) c FROM transactions WHERE is_duplicate = 1 GROUP BY merge_rule")},
        "enriched_live": conn.execute(
            "SELECT COUNT(*) c FROM transactions WHERE is_duplicate = 0 AND confidence = 'enriched'").fetchone()["c"],
        "months": {},
        "cards": {},
    }
    for period in ("2026-07", "2026-08", "2026-09", "2026-10"):
        o = analytics_overview(period=period)
        out["months"][period] = {k: o.get(k) for k in ("total", "gross_spend", "refunds", "fees_interest",
                                                        "outflows", "txn_count")}
    for c in cards_summary()["cards"]:
        ls = c.get("latest_statement") or {}
        out["cards"][c["last4"]] = {
            "status": c.get("payment_status"), "due": ls.get("due_date"), "due_src": ls.get("due_date_source"),
            "amount_due": ls.get("amount_due"), "remaining": c.get("remaining_due"),
            "unbilled": (c.get("unbilled") or {}).get("total"),
        }
    return out


def run(dry_run):
    conn = get_db()
    report = {"dry_run": dry_run, "before": snapshot(conn), "steps": []}

    def step(name, fn, writes_without_dry_run_option=False):
        t = time.time()
        if dry_run and writes_without_dry_run_option:
            report["steps"].append({"step": name, "skipped": "no dry-run mode"})
            return
        try:
            res = fn()
        except Exception as e:                       # report, never half-hide a failure
            res = {"error": f"{type(e).__name__}: {e}"}
        report["steps"].append({"step": name, "seconds": round(time.time() - t, 1), "result": res})

    from modules.sms.processing import (
        retire_merchant_refund_notice_rows, reprocess_history, refresh_existing_sms_rows, retry_quarantine,
        decode_html_entities,
    )
    from modules.pdf.store import fix_hdfc_plus_rows, fix_legacy_payment_rows, reparse_statements
    from modules.categorizer import recategorize_with_context
    from modules.kinds_backfill import backfill_kinds
    from modules.email.bank_alerts import backfill_alert_emails
    from modules.merge import run_merge_sweep, backfill_sms_refs, repair_inconsistent_groups, link_orphan_copies
    from modules.email.enrichment import rebuild_enrichment_links
    from modules.pairing import pair_rows
    import services.cards_service as cs

    step("retire_merchant_refund_notices", lambda: retire_merchant_refund_notice_rows(conn), True)
    step("fix_hdfc_plus_rows", lambda: fix_hdfc_plus_rows(conn, dry_run=dry_run))
    step("fix_legacy_payment_rows", lambda: fix_legacy_payment_rows(conn, dry_run=dry_run))
    step("recategorize_with_context", lambda: _brief(recategorize_with_context(conn, dry_run=dry_run)))
    step("backfill_kinds", lambda: _brief(backfill_kinds(conn, dry_run=dry_run)))
    step("reprocess_sms_history", lambda: _brief(reprocess_history(conn, dry_run=dry_run)))
    step("refresh_existing_sms_rows", lambda: _brief(refresh_existing_sms_rows(conn, dry_run=dry_run)))
    step("decode_html_entities", lambda: decode_html_entities(conn, dry_run=dry_run))
    step("reparse_statements", lambda: _brief(reparse_statements(conn, dry_run=dry_run)))
    step("backfill_alert_emails", lambda: _brief(backfill_alert_emails(conn, since="2025-10-01", dry_run=dry_run)))
    step("backfill_sms_refs", lambda: backfill_sms_refs(conn, dry_run=dry_run))
    step("repair_inconsistent_groups", lambda: repair_inconsistent_groups(conn, dry_run=dry_run))
    step("merge_sweep", lambda: run_merge_sweep(conn), True)
    step("link_orphan_copies", lambda: link_orphan_copies(conn, dry_run=dry_run))
    step("pair_rows", lambda: pair_rows(conn, dry_run=dry_run))
    step("rebuild_enrichment_links", lambda: rebuild_enrichment_links(conn, dry_run=dry_run))
    step("card_statements", lambda: cs.backfill_statements_logic(conn), True)
    step("card_payments", lambda: cs.backfill_payments_logic(conn), True)
    step("bank_statements_from_sms_and_email", lambda: cs.statements_from_sms_logic(conn), True)
    step("bbps_and_bank_debit_attribution", lambda: cs.attribute_bbps_payments_logic(conn), True)
    step("card_ids", lambda: cs.backfill_card_ids_logic(conn), True)
    step("reconcile_statements", lambda: cs.reconcile_statements_logic(conn=conn), True)
    step("retry_quarantine", lambda: retry_quarantine(conn), True)
    step("backfill_kinds_final", lambda: _brief(backfill_kinds(conn, dry_run=dry_run)))

    report["after"] = snapshot(conn)
    return report


def _brief(res):
    """Keep the report readable: drop long sample lists from step results."""
    if isinstance(res, dict):
        return {k: v for k, v in res.items() if k not in ("samples", "rows", "files", "details", "outcomes")}
    return res


if __name__ == "__main__":
    print(json.dumps(run("--dry-run" in sys.argv), indent=1, default=str))
