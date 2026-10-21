"""UPI mapping: turn raw VPAs into stable payee identities.

Background (iter-16, item C9 / tracker #146)
--------------------------------------------
`upi_mappings` sat empty since it was created. The only writer was
`POST /upi/classify`, a manual endpoint nothing ever called, so the feature existed
on paper and did nothing in practice.

What this module actually does, and — just as importantly — what it does not:

* Merchant VPAs (`blinkit.rzp@hdfcbank`, `zomato.payu@axisbank`, `paytmqr…@ptys`) are
  already resolved by the alias-driven categorizer. What was missing is that nothing
  *recorded* the resolution, so every pass re-derived it and the mapping table stayed
  empty. `seed_from_transactions()` harvests those confident resolutions into durable
  rows.

* Person-to-person VPAs (`jane.doe@axl`, `9000000000@ybl`) are genuinely
  un-inferable — there is no signal in the data that says who a person is. These are
  deliberately NOT auto-named. They are surfaced by `unmapped_payees()`, ranked by how
  much money and how many transactions run through them, so a human can name the
  handful that matter. Auto-guessing here would manufacture false confidence, which is
  the exact failure this project keeps having to undo.
"""
import re

from db import get_db

# A VPA is `handle@psp`. Handles legitimately contain dots, hyphens and digits.
# The trailing `(UPI 123456789012)` reference that some banks append is excluded.
VPA_RE = re.compile(r"\b([a-zA-Z0-9][a-zA-Z0-9._-]{1,63})@([a-zA-Z]{2,20})\b")

# Merchant resolutions worth persisting. Anything the categorizer left generic is a
# transfer or an unknown, and must not be written as if it were a known merchant.
GENERIC_MERCHANTS = {
    "UPI Transfer",
    "Unknown",
    "",
}
GENERIC_CATEGORIES = {"Uncategorized"}

# Categories that denote a PERSON or a bare transfer, never a merchant. The module
# promises persons are never auto-named, but the categorizer resolves user-defined
# family/self aliases to real names ("Devi Peddamam" / "Family Remittance"), which
# would otherwise be written here as mapped_type='merchant' and then propagated by
# apply_mappings onto every transaction through that VPA. A person's VPA can receive
# money for many reasons, so propagating one label is wrong. Skip these entirely;
# the categorizer still resolves them directly, so nothing is lost. (iter-16 C9 fix.)
NON_MERCHANT_CATEGORIES = {
    "Family Remittance",
    "Self Transfer",
    "Transfer",
    "Home Construction",
}


def extract_vpa(description: str):
    """Return (vpa, handle, psp) for the first VPA in a description, else (None, None, None)."""
    if not description:
        return (None, None, None)
    m = VPA_RE.search(description)
    if not m:
        return (None, None, None)
    handle, psp = m.group(1), m.group(2)
    return (f"{handle}@{psp}", handle, psp)


def seed_from_transactions() -> dict:
    """Persist merchant VPAs the categorizer already resolves confidently.

    Idempotent: uses INSERT OR IGNORE against the UNIQUE(upi_id) constraint, so
    running it repeatedly converges rather than duplicating.
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT raw_description, merchant, category, COUNT(*) AS n "
        "FROM transactions "
        "WHERE raw_description LIKE '%@%' AND is_duplicate = 0 "
        "GROUP BY raw_description, merchant, category"
    ).fetchall()

    inserted = skipped_generic = skipped_no_vpa = 0
    for r in rows:
        vpa, _handle, _psp = extract_vpa(r["raw_description"])
        if not vpa:
            skipped_no_vpa += 1
            continue
        merchant = (r["merchant"] or "").strip()
        category = (r["category"] or "").strip()
        if (merchant in GENERIC_MERCHANTS
                or category in GENERIC_CATEGORIES
                or category in NON_MERCHANT_CATEGORIES):
            skipped_generic += 1
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO upi_mappings (upi_id, mapped_type, brand, category, notes) "
            "VALUES (?, 'merchant', ?, ?, 'auto-seeded from resolved transactions')",
            (vpa, merchant, category),
        )
        inserted += cur.rowcount or 0

    conn.commit()
    return {
        "inserted": inserted,
        "skipped_generic": skipped_generic,
        "skipped_no_vpa": skipped_no_vpa,
        "total_mappings": conn.execute("SELECT COUNT(*) c FROM upi_mappings").fetchone()["c"],
    }


def apply_mappings() -> dict:
    """Apply known mappings to transactions still sitting on a generic merchant.

    This is the part that makes the table earn its keep: once a VPA is known, every
    past and future transaction through it gets the same name, including ones the
    alias list never covered.
    """
    conn = get_db()
    mappings = conn.execute(
        "SELECT upi_id, brand, category FROM upi_mappings "
        "WHERE upi_id IS NOT NULL AND brand IS NOT NULL"
    ).fetchall()

    updated = 0
    for m in mappings:
        cur = conn.execute(
            "UPDATE transactions SET merchant = ?, category = ?, confidence = 'high' "
            "WHERE raw_description LIKE '%' || ? || '%' "
            "AND is_duplicate = 0 "
            "AND (merchant IN ('UPI Transfer','Unknown') OR category = 'Uncategorized')",
            (m["brand"], m["category"], m["upi_id"]),
        )
        updated += cur.rowcount or 0

    conn.commit()
    return {"mappings_applied": len(mappings), "transactions_updated": updated}


def unmapped_payees(limit: int = 40) -> dict:
    """Un-named VPAs ranked by money and frequency — the queue for a human to label.

    Deliberately ranked by total value rather than count: a recurring rent transfer
    matters more than a dozen small ones, and it is the sort of thing worth naming.
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT raw_description, COUNT(*) AS n, SUM(amount) AS total, MAX(date) AS last_seen "
        "FROM transactions "
        "WHERE raw_description LIKE '%@%' AND is_duplicate = 0 AND txn_type = 'debit' "
        "AND (merchant IN ('UPI Transfer','Unknown') OR category = 'Uncategorized') "
        "GROUP BY raw_description ORDER BY SUM(amount) DESC LIMIT ?",
        (limit,),
    ).fetchall()

    known = {r["upi_id"] for r in conn.execute("SELECT upi_id FROM upi_mappings").fetchall()}
    out = []
    for r in rows:
        vpa, handle, psp = extract_vpa(r["raw_description"])
        if not vpa or vpa in known:
            continue
        out.append({
            "upi_id": vpa,
            "handle": handle,
            "psp": psp,
            "transactions": r["n"],
            "total_amount": round(r["total"] or 0, 2),
            "last_seen": r["last_seen"],
        })
    return {"payees": out, "total": len(out)}


def run_upi_classification() -> dict:
    """Full pass: seed from resolved transactions, then apply mappings. Queue-callable."""
    seeded = seed_from_transactions()
    applied = apply_mappings()
    return {"seeded": seeded, "applied": applied}
