"""iter-203 data steps for the categories + recurring lane (E4, E5, E6, E10).

Each step is `step_<name>(conn, dry_run=True) -> dict`: a dry run reports exactly what it would
change and writes nothing; dry_run=False applies it. Every step is idempotent (a second run finds
nothing left to do) and leaves rows the owner re-filed by hand alone. The deploy runner calls them
after the owner's private data step (payee rules / cancellations, never committed).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.categorizer import recategorize_with_context  # noqa: E402

SENT_TO_PEOPLE_CATEGORY = "Transfer"


def step_recategorize_sent_to_people(conn, dry_run=True):
    """E4 + E10: rows filed "Transfer" (Sent to people) that the iter-203 categorizer files somewhere
    else — businesses paid by UPI (business names, merchant UPI IDs, UPI AutoPay mandates), payee
    rules, spelling variants of a known payee. Only rows LEAVING "Transfer" are touched; every other
    drift a full recategorise would apply is out of this step's scope (skipped['filtered']).

    Returns {dry_run, rows_moved, live_rows_moved, live_amount_moved, by_transition, rows: [...]}."""
    moved = []

    def leaving_transfer(row, merchant, category):
        if (row["category"] or "") != SENT_TO_PEOPLE_CATEGORY or category == SENT_TO_PEOPLE_CATEGORY:
            return False
        moved.append((row["id"], merchant, category))
        return True

    res = recategorize_with_context(conn, dry_run=dry_run, samples_per_transition=0, row_filter=leaving_transfer)
    rows = []
    live_amount = 0.0
    live_rows = 0
    for txn_id, merchant, category in moved:
        r = conn.execute(
            "SELECT id, date, amount, source, is_duplicate, raw_description FROM transactions WHERE id = ?",
            (txn_id,),
        ).fetchone()
        rows.append({"id": r["id"], "date": r["date"], "amount": r["amount"], "source": r["source"],
                     "is_duplicate": r["is_duplicate"], "raw_description": r["raw_description"],
                     "to_category": category, "to_merchant": merchant})
        if not r["is_duplicate"]:
            live_rows += 1
            live_amount += r["amount"] or 0.0
    return {
        "dry_run": dry_run,
        "rows_moved": len(rows),
        "live_rows_moved": live_rows,
        "live_amount_moved": round(live_amount, 2),
        "by_transition": res["by_transition"],
        "rows": rows,
    }


def step_hdfc_emi_interest_kind(conn, dry_run=True):
    """E6: HDFC "OFFUS EMI,INT NBR:x,<loan>" lines are an instalment's interest, but were stored with
    kind 'emi_principal' and so left out of spend. Re-kinds them 'interest' (never touches PRIN lines).

    Returns {dry_run, rows, live_rows, live_amount, by_card}."""
    from modules.txn_store import _EMI_INTEREST_LINE

    rows = [r for r in conn.execute(
        "SELECT id, date, amount, card_last4, is_duplicate, raw_description FROM transactions "
        "WHERE kind = 'emi_principal' AND txn_type = 'debit'"
    ).fetchall() if _EMI_INTEREST_LINE.search(r["raw_description"] or "")]
    if not dry_run and rows:
        conn.executemany("UPDATE transactions SET kind = 'interest' WHERE id = ?", [(r["id"],) for r in rows])
        conn.commit()
    live = [r for r in rows if not r["is_duplicate"]]
    by_card = {}
    for r in live:
        by_card[r["card_last4"]] = round(by_card.get(r["card_last4"], 0.0) + r["amount"], 2)
    return {"dry_run": dry_run, "rows": len(rows), "live_rows": len(live),
            "live_amount": round(sum(r["amount"] for r in live), 2), "by_card": by_card,
            "ids": [r["id"] for r in rows]}
