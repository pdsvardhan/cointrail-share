"""Transaction query and update routes."""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from db import get_db
from modules import payee_rules
from modules.categorizer import OWNER_CATEGORIES, card_numbers, known_categories, recategorize_with_context
from modules.kinds_backfill import kind_for_row
from services import recurring_service
from services.recurring_service import detect_recurring as _detect_recurring

router = APIRouter()

# Kinds that derive_kind() reads off the category (Finance -> bill_payment, Bank Charges -> fee,
# ATM Withdrawal -> atm, otherwise purchase / debit / transfer_in by context). When the owner
# re-files a row these must follow the new category: a "Finance" row re-filed as Shopping would
# otherwise stay a bill_payment and never count as spend (iter-18 H5b). Kinds read from the text
# (refund, reversal, cashback, interest, emi_principal, emi_conversion) describe what happened
# and stay as they are.
_CATEGORY_DERIVED_KINDS = {"bill_payment", "fee", "atm", "purchase", "debit", "transfer_in"}


@router.get("/transactions")
def list_transactions(
    source: Optional[str] = None,
    category: Optional[str] = None,
    bank: Optional[str] = None,
    card_last4: Optional[str] = None,
    txn_type: Optional[str] = None,
    confidence: Optional[str] = None,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    search: Optional[str] = None,
    include_duplicates: bool = False,
    limit: int = 100,
    offset: int = 0,
):
    """Query transactions with filters."""
    conn = get_db()
    query = "SELECT * FROM transactions WHERE 1=1"
    params = []

    if not include_duplicates:
        query += " AND is_duplicate = 0"
    if source:
        query += " AND source = ?"
        params.append(source)
    if category:
        query += " AND category = ?"
        params.append(category)
    if bank:
        query += " AND bank = ?"
        params.append(bank)
    if card_last4:
        query += " AND card_last4 = ?"
        params.append(card_last4)
    if txn_type:
        query += " AND txn_type = ?"
        params.append(txn_type)
    if confidence:
        query += " AND confidence = ?"
        params.append(confidence)
    if date_from:
        query += " AND date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND date <= ?"
        params.append(date_to)
    if min_amount is not None:
        query += " AND amount >= ?"
        params.append(min_amount)
    if max_amount is not None:
        query += " AND amount <= ?"
        params.append(max_amount)
    if search:
        query += " AND (merchant LIKE ? OR raw_description LIKE ? OR category LIKE ?)"
        search_term = f"%{search}%"
        params.extend([search_term, search_term, search_term])

    count_query = query.replace("SELECT *", "SELECT COUNT(*) as c", 1)
    total = conn.execute(count_query, params).fetchone()["c"]

    query += " ORDER BY date DESC, created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(query, params).fetchall()
    return {"total": total, "limit": limit, "offset": offset, "transactions": [dict(r) for r in rows]}


_TXN_FIELDS = ("id", "date", "amount", "txn_type", "merchant", "category", "confidence", "kind",
               "raw_description", "card_last4", "source", "is_duplicate")


def _txn_out(conn, txn_id):
    r = conn.execute(f"SELECT {', '.join(_TXN_FIELDS)} FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    return {k: r[k] for k in _TXN_FIELDS} if r else None


def _rule_out(conn, rule):
    if rule is None:
        return None
    out = payee_rules.as_dict(rule)
    out["matching_transactions"] = payee_rules.matching_count(conn, rule["key_type"], rule["key"])
    return out


@router.put("/transactions/{txn_id}")
def update_transaction(txn_id: int, data: dict):
    """Re-file a transaction (iter-203 E3).

    Body: {"category": str, "merchant"?: str, "apply_to_payee"?: bool}.
    The row becomes a manual override (never relabelled by a later pass). It never learns a rule by
    itself (E9). With apply_to_payee the owner asks for a payee rule keyed on this row's UPI ID /
    counterparty account / exact payee name: every stored row of that payee is relabelled (except
    rows re-filed by hand) and future ones are labelled at ingest. Generic text ("DEBIT") names no
    payee; then no rule is made and `rule_refused` says why.

    Returns {status, transaction, rule, rule_refused, relabelled, relabelled_ids}."""
    conn = get_db()
    txn = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    if not txn:
        raise HTTPException(404, "Transaction not found")

    new_category = data.get("category")
    new_merchant = data.get("merchant")
    if new_category is not None:
        if not isinstance(new_category, str) or not new_category.strip():
            raise HTTPException(400, "category must be a non-empty string")
        new_category = new_category.strip()
    rule = refused = None
    relabelled = []

    if new_category:
        conn.execute(
            "INSERT INTO category_overrides (transaction_id, old_category, new_category, old_merchant, new_merchant) "
            "VALUES (?, ?, ?, ?, ?)",
            (txn_id, txn["category"], new_category, txn["merchant"], new_merchant)
        )
        updates = ["category = ?", "confidence = 'manual'"]
        params = [new_category]
        if new_merchant:
            updates.append("merchant = ?")
            params.append(new_merchant)
        params.append(txn_id)
        conn.execute(f"UPDATE transactions SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()

        if txn["kind"] in _CATEGORY_DERIVED_KINDS:
            row = conn.execute(
                "SELECT source, card_last4, txn_type, category, raw_description, raw_text "
                "FROM transactions WHERE id = ?", (txn_id,)
            ).fetchone()
            new_kind = kind_for_row(row, card_numbers(conn))
            if new_kind != txn["kind"]:
                conn.execute("UPDATE transactions SET kind = ? WHERE id = ?", (new_kind, txn_id))
                conn.commit()

        if data.get("apply_to_payee"):
            key_type, key, label, refused = payee_rules.payee_key(txn["raw_description"], conn)
            if key_type:
                rule, relabelled = payee_rules.save_rule(
                    conn, key_type, key, label, new_category, merchant_name=new_merchant, txn_id=txn_id)

    return {
        "status": "updated",
        "transaction": _txn_out(conn, txn_id),
        "rule": _rule_out(conn, rule),
        "rule_refused": refused,
        "relabelled": len(relabelled),
        "relabelled_ids": relabelled,
    }


@router.get("/transactions/{txn_id}/payee")
def get_transaction_payee(txn_id: int):
    """What an "apply to all payments to this payee" rule would key on for this row (iter-203 E3):
    {transaction_id, payee: {key_type, key, label} | null, refused: reason | null,
    rule: the existing rule for that payee | null, matching_transactions: live rows of the payee}."""
    conn = get_db()
    txn = conn.execute("SELECT id, raw_description FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    if not txn:
        raise HTTPException(404, "Transaction not found")
    key_type, key, label, refused = payee_rules.payee_key(txn["raw_description"], conn)
    if not key_type:
        return {"transaction_id": txn_id, "payee": None, "refused": refused, "rule": None,
                "matching_transactions": 0}
    existing = conn.execute("SELECT * FROM payee_rules WHERE key_type = ? AND key = ?", (key_type, key)).fetchone()
    return {
        "transaction_id": txn_id,
        "payee": {"key_type": key_type, "key": key, "label": label},
        "refused": None,
        "rule": _rule_out(conn, existing),
        "matching_transactions": payee_rules.matching_count(conn, key_type, key),
    }


@router.get("/payee-rules")
def list_payee_rules():
    """Every payee rule the owner made (iter-203 E3/E9), newest first, with how many live rows each
    covers."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM payee_rules ORDER BY id DESC").fetchall()
    rules = [_rule_out(conn, r) for r in rows]
    return {"rules": rules, "total": len(rules)}


@router.delete("/payee-rules/{rule_id}")
def delete_payee_rule(rule_id: int):
    """Remove a payee rule; the rows it labelled get the categorizer's own label back (rows re-filed
    by hand keep theirs). Returns {status, rule, relabelled}."""
    conn = get_db()
    rule = conn.execute("SELECT * FROM payee_rules WHERE id = ?", (rule_id,)).fetchone()
    if not rule:
        raise HTTPException(404, "Payee rule not found")
    out = payee_rules.as_dict(rule)
    relabelled = payee_rules.delete_rule(conn, rule_id)
    return {"status": "deleted", "rule": out, "relabelled": len(relabelled or [])}


@router.get("/category-choices")
def category_choices():
    """Categories a transaction can be filed under, each with the group it counts in on the Expenses
    page: spend | sent_to_people | investments | loans | excluded (iter-203 E3)."""
    from routes.analytics import category_group
    conn = get_db()
    cats = [{"name": n, "group": category_group(n)} for n in known_categories(conn)]
    return {"categories": cats, "owner_categories": list(OWNER_CATEGORIES)}


@router.post("/recategorize-all")
def recategorize_all(dry_run: bool = True):
    """Re-run the categorizer over history with each row's source context (iter-18 M14).

    Dry run by default: returns the old→new category transition table and samples without
    writing anything; POST /recategorize-all?dry_run=false applies it. Manual overrides,
    UPI-mapping labels and email-receipt labels are never changed (see
    modules.categorizer.recategorize_with_context)."""
    return recategorize_with_context(get_db(), dry_run=dry_run)


@router.get("/recurring/detect")
def detect_recurring_endpoint(include_ended: bool = False):
    """Auto-detect recurring expenses, subscriptions, and EMIs.

    Default returns active series only; include_ended=true adds ended
    subscriptions and completed EMIs (each row carries `status`).
    """
    return _detect_recurring(include_ended=include_ended)


@router.get("/emi-plans")
def list_emi_plans(card_last4: Optional[str] = None, include_completed: bool = False):
    """EMI plans (iter-203 E6): one item per converted purchase — merchant, purchase, tenure, rate,
    monthly payment (principal + interest + GST), instalments paid x of n, end date, interest paid so
    far, each instalment. Active plans only unless include_completed=true; card_last4 filters."""
    from services.emi_plans import detect_emi_plans
    plans = detect_emi_plans(get_db())
    if card_last4:
        plans = [p for p in plans if p["card_last4"] == card_last4]
    if not include_completed:
        plans = [p for p in plans if p["status"] == "active"]
    return {"plans": plans, "total": len(plans)}


@router.get("/recurring/cancellations")
def list_recurring_cancellations():
    """Series the owner marked cancelled (iter-203 E5)."""
    rows = recurring_service.list_cancellations(get_db())
    return {"cancellations": rows, "total": len(rows)}


@router.post("/recurring/cancellations")
def cancel_recurring(data: dict):
    """Mark a recurring series cancelled (iter-203 E5).

    Body: {"series_key": str (from /recurring/detect), "cancelled_on"?: "YYYY-MM-DD" (default today
    IST), "display_name"?: str, "notes"?: str}. The series leaves the active list and its totals; a
    charge dated after cancelled_on raises a /health warning and a digest line."""
    try:
        row = recurring_service.cancel_series(get_db(), data.get("series_key"), data.get("cancelled_on"),
                                              data.get("display_name"), data.get("notes"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "cancelled", "cancellation": row}


@router.delete("/recurring/cancellations/{cancellation_id}")
def uncancel_recurring(cancellation_id: int):
    """Undo a cancellation: the series is active again (iter-203 E5)."""
    row = recurring_service.uncancel_series(get_db(), cancellation_id)
    if row is None:
        raise HTTPException(404, "Cancellation not found")
    return {"status": "restored", "cancellation": row}
