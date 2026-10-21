"""
`kind` for rows written before iter-18 (HP4 / H5b).

insert_transaction() gives every new row a kind (purchase, refund, reversal, cashback, fee,
interest, emi_principal, emi_conversion, atm, transfer_in, debit, bill_payment). The ~3,500 rows
already in the database have kind NULL. The analytics endpoints classify a NULL-kind row on the
fly with kind_for_row(), and backfill_kinds() writes the very same answer into the column, so the
spend totals are identical before and after the backfill.

Which text derive_kind() reads:
  - credits from SMS / email: the full message (raw_text). The stored description of a credit is
    often a placeholder ("Unknown credit", "HSBC credit") while the message itself says
    "refunded", "towards reversal/cashback", "has been reversed" — the only evidence that money
    came back rather than came in.
  - debits, and statement-PDF rows: the description, exactly what insert_transaction() reads for
    new rows. A debit SMS carries bank boilerplate that is not about the transaction: every ICICI
    card spend SMS ends "To convert this txn to EMI ... Know more about EMI conversion", and on
    the full text derive_kind() files those purchases as emi_principal (16 live rows, Rs 3.4 lakh
    of ordinary Croma/Flipkart/Reliance Retail spends would have dropped out of spend).
"""
from collections import Counter

from modules.categorizer import card_numbers, context_for_row
from modules.txn_store import derive_kind

MESSAGE_SOURCES = {"sms", "email"}


def kind_text(source, txn_type, raw_description, raw_text):
    if txn_type == "credit" and source in MESSAGE_SOURCES and raw_text:
        return raw_text
    return raw_description


def kind_for_row(row, cards):
    """The kind insert_transaction() would have stored for this row.

    `row` needs source, card_last4, txn_type, category, raw_description, raw_text; `cards` is
    categorizer.card_numbers(conn)."""
    txn_type = row["txn_type"] or "debit"
    ctx = context_for_row(row["source"], row["card_last4"], row["raw_text"] or row["raw_description"], cards)
    text = kind_text(row["source"], txn_type, row["raw_description"], row["raw_text"])
    return derive_kind(txn_type, row["category"], text, ctx)


def backfill_kinds(conn, dry_run=True):
    """Set transactions.kind where it is NULL. Never overwrites a kind that is already set.

    Returns {dry_run, rows, updated, by_kind: {kind: n}, by_type_kind: {"debit:purchase": n},
    amount_by_kind: {kind: rupees}} for the rows that were (or, in a dry run, would be) set."""
    cards = card_numbers(conn)
    rows = conn.execute(
        "SELECT id, source, card_last4, txn_type, category, raw_description, raw_text, amount "
        "FROM transactions WHERE kind IS NULL ORDER BY id"
    ).fetchall()
    by_kind = Counter()
    by_type_kind = Counter()
    amount_by_kind = Counter()
    updates = []
    for r in rows:
        kind = kind_for_row(r, cards)
        by_kind[kind] += 1
        by_type_kind[f"{r['txn_type'] or 'debit'}:{kind}"] += 1
        amount_by_kind[kind] += r["amount"] or 0
        updates.append((kind, r["id"]))
    if not dry_run and updates:
        # The kind IS NULL guard keeps a concurrent insert_transaction() row's own kind intact.
        conn.executemany("UPDATE transactions SET kind = ? WHERE id = ? AND kind IS NULL", updates)
        conn.commit()
    return {
        "dry_run": dry_run,
        "rows": len(rows),
        "updated": 0 if dry_run else len(updates),
        "by_kind": dict(by_kind.most_common()),
        "by_type_kind": dict(by_type_kind.most_common()),
        "amount_by_kind": {k: round(v, 2) for k, v in amount_by_kind.most_common()},
    }
