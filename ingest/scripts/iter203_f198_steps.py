"""
Iteration 203, lane F198: the history repair behind the iteration-198 re-verify fixes (HP3 / H4).

Every step is `step_<name>(conn, dry_run=True) -> dict`, generic (no owner data), idempotent, and
writes nothing when dry_run is true (it works on an in-memory copy). The iteration-203 post-deploy
runner calls it after `unique_link_review` and before `repair_loan_repayments`:

  step_far_guess_repair   HP3/H4  existing SMS <-> email links made as a far guess against nearer
                                  evidence are undone, references stamped across a far guess are
                                  removed, then the merge sweep re-pairs everything with what is near
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _work_copy(conn, dry_run):
    if not dry_run:
        return conn
    work = sqlite3.connect(":memory:", isolation_level=None)
    conn.backup(work)
    work.row_factory = sqlite3.Row
    return work


def far_guesses(conn):
    """Weakly linked SMS / email copies whose group partner of the other alert source is more than
    CROSS_ALERT_MAX_SECONDS away while a nearer alert provably belongs to another transaction — the
    links today's rules refuse — and winners carrying a reference inherited across such a far guess."""
    from modules import merge
    alias = merge._card_alias_map(conn)
    text = merge._text_cache(conn)
    against, stamped = [], []
    for d in conn.execute(f"SELECT {merge._ROW_COLS} FROM transactions WHERE is_duplicate = 1 "
                          "AND source IN ('sms', 'email') AND merge_rule IN ('unique', 'merchant') "
                          "AND matched_txn_id IS NOT NULL ORDER BY date, id").fetchall():
        w = merge._row(conn, d["matched_txn_id"])
        if w is None:
            continue
        group = merge._group(conn, d)
        rest = [m for m in group if m["id"] != d["id"]]
        far = [m for m in rest if m["source"] in ("sms", "email") and m["source"] != d["source"]
               and merge.CROSS_ALERT_MAX_SECONDS < merge._gap_s(conn, d, m) < float("inf")]
        if not far or merge._confirmed(text, [d], rest):
            continue                     # proof (same printed time / balance) beats distance
        inside = {m["id"] for m in group}
        near_other = False
        for z in merge._alert_neighbours(conn, alias, d):
            if z["id"] in inside:
                continue
            # an alert next to the copy that is provably not its group's movement, or next to the far
            # partner and provably not the copy's
            if merge._gap_s(conn, z, d) <= merge.CROSS_ALERT_MAX_SECONDS and merge._against(conn, z, rest, text):
                near_other = True
            elif (any(merge._gap_s(conn, z, m) <= merge.CROSS_ALERT_MAX_SECONDS for m in far)
                  and merge._against(conn, z, [d], text)):
                near_other = True
            if near_other:
                break
        if near_other:
            against.append({"copy": d["id"], "winner": w["id"], "date": d["date"], "amount": d["amount"],
                            "card_last4": d["card_last4"], "rule": d["merge_rule"]})
        ref = (d["ref_no"] or "").strip().upper()
        if (ref and w["source"] == "sms" and (w["ref_no"] or "").strip().upper() == ref
                and merge._without_stamp(w, {ref})["ref_no"] is None):
            stamped.append({"winner": w["id"], "ref_from": d["id"], "date": d["date"], "amount": d["amount"]})
    return against, stamped


def step_far_guess_repair(conn, dry_run=True):
    from modules.merge import run_merge_sweep
    work = _work_copy(conn, dry_run)
    try:
        against, stamped = far_guesses(work)
        live_before = work.execute("SELECT COUNT(*) c FROM transactions WHERE is_duplicate = 0").fetchone()["c"]
        if against:
            work.executemany("UPDATE transactions SET is_duplicate = 0, matched_txn_id = NULL, merge_rule = NULL "
                             "WHERE id = ?", [(a["copy"],) for a in against])
        if stamped:
            work.executemany("UPDATE transactions SET ref_no = NULL WHERE id = ?", [(s["winner"],) for s in stamped])
        work.commit()
        sweep = run_merge_sweep(work)
        live_after = work.execute("SELECT COUNT(*) c FROM transactions WHERE is_duplicate = 0").fetchone()["c"]
    finally:
        if dry_run:
            work.close()
    return {"dry_run": dry_run, "far_guesses_undone": against, "references_unstamped": stamped,
            "sweep": sweep, "live_before": live_before, "live_after": live_after}
