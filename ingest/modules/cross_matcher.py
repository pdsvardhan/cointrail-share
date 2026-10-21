"""
Cross-source matcher — kept as a thin compatibility shim (iter-18).

The v2 matcher (adr-005) refused to de-duplicate any SMS whose category was transfer-like, and
categories are guesses, so 132 card purchases were counted twice. Identity is now decided in
modules.merge from card, amount, date, direction and reference number (adr-006); this module only
keeps the old entry points working for the hourly `cross_match` task and POST /cross-match.
"""
from db import get_db
from modules.merge import run_merge_sweep


def run_cross_match():
    """Hourly sweep: link copies of the same purchase that arrived from different sources."""
    from modules.pairing import pair_rows
    stats = run_merge_sweep(get_db())
    pairs = pair_rows(get_db())          # own-account transfers, card autopays, reversals (iter-18)
    return {"matched": stats["linked"], "checked": stats["checked"], "by_rule": stats["by_rule"], "pairing": pairs}


def get_duplicate_stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM transactions").fetchone()["c"]
    duplicates = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE is_duplicate = 1").fetchone()["c"]
    unmatched_card = conn.execute(
        "SELECT COUNT(*) as c FROM transactions WHERE source = 'card' AND matched_txn_id IS NULL AND is_duplicate = 0"
    ).fetchone()["c"]
    by_rule = {r["merge_rule"] or "legacy-v2": r["n"] for r in conn.execute(
        "SELECT merge_rule, COUNT(*) n FROM transactions WHERE is_duplicate = 1 GROUP BY merge_rule")}
    return {"total": total, "duplicates": duplicates, "unmatched_card": unmatched_card, "by_rule": by_rule}
