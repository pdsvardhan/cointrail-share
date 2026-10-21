"""Credit card, statement, and payment routes."""
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Annotated, Optional

from db import get_db
from modules.categorizer import card_numbers
from routes.analytics import GROUP_EXCLUDED, classify
from util.timez import today_ist
from services.cards_service import (
    _CARD_COLS, _billing_primary, _group_last4s,
    backfill_payments_logic,
    backfill_statements_logic, statements_from_sms_logic,
    dedup_payments_logic,
    reconcile_statements_logic, attribute_bbps_payments_logic, backfill_card_ids_logic,
    build_cards_summary, statement_rows, latest_statement_row, unbilled_tail_start,
    update_card, create_card, CardConfigError, CardConflict,
)

router = APIRouter()


class CardCreate(BaseModel):
    bank: str
    last4: str
    card_name: Optional[str] = None
    network: Optional[str] = None
    statement_day: int
    due_day: int


class CardUpdate(BaseModel):
    card_name: Optional[str] = None
    statement_day: Optional[int] = None
    due_day: Optional[int] = None
    pdf_card_last4: Optional[str] = None
    active: Optional[bool] = None
    replaced_by_card_id: Optional[int] = None


def _sent_fields(model):
    """Only the fields the client actually sent (pydantic v1 and v2)."""
    dump = getattr(model, "model_dump", None)
    return dump(exclude_unset=True) if dump else model.dict(exclude_unset=True)


@router.get("")
def list_cards():
    conn = get_db()
    rows = conn.execute("SELECT * FROM cards WHERE active = 1").fetchall()
    return {"cards": [dict(r) for r in rows]}


@router.post("")
def add_card(body: CardCreate):
    """iter-18 M10: add a card (e.g. a reissued card's new last-4) without a code change; the
    cards config file in the data directory is rewritten so it survives restarts."""
    try:
        return create_card(get_db(), _sent_fields(body))
    except CardConflict as e:
        raise HTTPException(409, str(e))
    except CardConfigError as e:
        raise HTTPException(400, str(e))


@router.put("/{card_id}")
def edit_card(card_id: int, body: CardUpdate):
    """iter-18 M10: edit a card's name, statement/due day, PDF alias, active flag or successor."""
    try:
        return update_card(get_db(), card_id, _sent_fields(body))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except CardConfigError as e:
        raise HTTPException(400, str(e))


@router.get("/summary")
def cards_summary():
    """Per-card bill status. Status and the current cycle are decided on today's IST date; see
    services.cards_service.build_cards_summary for the fields (iter-18 H1/H2/M11)."""
    return build_cards_summary(get_db(), today_ist())


@router.get("/{card_id}/statements")
def card_statements(card_id: int):
    return {"statements": statement_rows(get_db(), card_id)}


def _r(x):
    return round(x + 0.0, 2)


@router.get("/{card_id}/transactions")
def card_transactions(
    card_id: int,
    period: Optional[str] = None,
    date_from: Annotated[Optional[str], Query(alias="from")] = None,
    date_to: Annotated[Optional[str], Query(alias="to")] = None,
    limit: int = 500,
):
    """A card's transactions for the detail page, plus the sidebar's aggregates (iteration 203 E2).

    - Every card billed together (an add-on and its primary) is shown on either card's page (M9).
    - period=unbilled is exactly the summary's Unbilled set: after the latest closed statement,
      plus the cycle's last days that the statement did not bill (M7).
    - Totals, categories and merchants count spending only (analytics.classify): refunds and
      cashback net inside their category, bill payments and EMI principal are listed but never
      added up (B3), and they cover every matching row, not just the `limit` listed (M8).
    """
    conn = get_db()
    card = conn.execute(f"SELECT {_CARD_COLS} FROM cards WHERE id=?", (card_id,)).fetchone()
    if not card:
        return {"transactions": [], "error": "Card not found"}
    group = _group_last4s(conn, card["last4"])
    # iter-18: bank alert emails are a transaction source too (SBI/ICICI spends with no SMS);
    # cross-channel copies are linked by the merge rules, so is_duplicate=0 shows each once.
    where = "source IN ('card','sms','email') AND is_duplicate=0"
    params = []
    unbilled_since = None
    if period == "unbilled":
        # the latest CLOSED statement by close date of the bill's primary card, the same one
        # /cards/summary reports
        stmt = latest_statement_row(conn, _billing_primary(conn, card)["id"], today_ist())
        unbilled_since = (stmt["billing_end"] or "") if stmt else ""
        per_card = []
        for last4 in group:
            tail = unbilled_tail_start(conn, last4, unbilled_since)
            per_card.append("(card_last4=? AND (date > ? OR (? <> '' AND source IN ('sms','email') "
                            "AND date > ? AND date <= ?)))")
            params += [last4, unbilled_since, tail, tail, unbilled_since]
        where += " AND (" + " OR ".join(per_card) + ")"
    else:
        where += f" AND card_last4 IN ({','.join('?' * len(group))})"
        params += group
        if date_from:
            where += " AND date >= ?"
            params.append(date_from)
        if date_to:
            where += " AND date <= ?"
            params.append(date_to)
    rows = conn.execute(
        "SELECT id, amount, raw_description, merchant, category, date, txn_type, confidence, kind, source, "
        f"card_last4, raw_text FROM transactions WHERE {where} ORDER BY date DESC, id DESC",
        params,
    ).fetchall()

    cards = card_numbers(conn)
    categories = defaultdict(lambda: {"total": 0.0, "count": 0, "merchants": defaultdict(lambda: [0.0, 0])})
    merchants = defaultdict(lambda: {"total": 0.0, "count": 0, "category": None})
    gross = refunds = 0.0
    refund_count = 0
    for r in rows:
        c = classify(r, cards)
        if c is None or c[0] == GROUP_EXCLUDED:
            continue
        signed = c[1]
        cat = r["category"] or "Uncategorized"
        name = r["merchant"] or r["raw_description"] or "Unknown"
        entry, m = categories[cat], merchants[name]
        entry["total"] += signed
        entry["merchants"][name][0] += signed
        m["total"] += signed
        m["category"] = m["category"] or cat
        if signed > 0:
            gross += signed
            entry["count"] += 1
            entry["merchants"][name][1] += 1
            m["count"] += 1
        else:
            refunds -= signed
            refund_count += 1

    breakdown = sorted(
        [{"category": cat, "total": _r(v["total"]), "count": v["count"],
          "merchants": sorted([{"name": n, "total": _r(t), "count": k} for n, (t, k) in v["merchants"].items()],
                              key=lambda x: -x["total"])}
         for cat, v in categories.items()],
        key=lambda x: -x["total"])
    listed = rows[:max(0, limit)]
    out = {
        "card_last4s": group,
        "transactions": [{k: r[k] for k in r.keys() if k != "raw_text"} for r in listed],
        "count": len(listed),
        "total_count": len(rows),
        "total": _r(gross - refunds),
        "gross": _r(gross),
        "refunds": {"total": _r(refunds), "count": refund_count},
        "categories": {c["category"]: c["total"] for c in breakdown},
        "category_breakdown": breakdown,
        "top_merchants": sorted(
            [{"name": k, "total": _r(v["total"]), "count": v["count"], "category": v["category"]}
             for k, v in merchants.items()],
            key=lambda x: -x["total"]
        )[:20],
    }
    if unbilled_since is not None:
        out["unbilled_since"] = unbilled_since
    return out


@router.post("/statements/backfill")
def backfill_statements():
    return backfill_statements_logic()


@router.post("/statements/from-sms")
def statements_from_sms():
    """Statement totals, bank due dates and minimum dues from statement SMS and — iter-18 HP5 —
    bank and CRED bill emails."""
    return statements_from_sms_logic()


@router.post("/statements/reconcile")
def reconcile_statements():
    """iter-16 C1: flag statements whose parsed-transaction sum disagrees with the bank's
    printed total by more than 1%."""
    return reconcile_statements_logic()


@router.post("/payments/attribute-bbps")
def attribute_bbps():
    """iter-16 C4/C5: pin card-less SBI BBPS payments to a card by matching the paid amount
    to that card's authoritative statement total. iter-18 M1: then attribute CRED debits on
    the owner's bank accounts to the statement(s) they paid."""
    return attribute_bbps_payments_logic()


@router.post("/backfill-card-ids")
def backfill_card_ids():
    """iter-16 C2: populate transactions.card_id from card_last4 for active cards."""
    return backfill_card_ids_logic()


@router.get("/statements/needs-review")
def statements_needing_review():
    """iter-16 C1: statements whose printed total and parsed sum diverge — the honest
    list of where a parse is probably incomplete."""
    conn = get_db()
    rows = conn.execute(
        "SELECT s.id, c.bank, c.last4, s.month, s.year, s.stated_total, s.computed_total, "
        "s.variance_pct, s.total_source FROM statements s JOIN cards c ON c.id=s.card_id "
        "WHERE s.needs_review=1 ORDER BY s.variance_pct DESC"
    ).fetchall()
    return {"statements": [dict(r) for r in rows], "count": len(rows)}


@router.post("/payments/backfill")
def backfill_payments():
    """Card-bill payments from bank SMS and — iter-18 M1 — CRED / ICICI payment emails."""
    return backfill_payments_logic()


@router.post("/payments/dedup")
def dedup_payments():
    return dedup_payments_logic()


@router.get("/monthly-summary")
def monthly_summary():
    """Get calendar-month spending summary across all cards (calendar months in IST, iter-18 M8)."""
    conn = get_db()

    now = today_ist()
    current_month_start = f"{now.year}-{now.month:02d}-01"

    prev_month = now.month - 1
    prev_year = now.year
    if prev_month < 1:
        prev_month = 12
        prev_year -= 1
    last_month_start = f"{prev_year}-{prev_month:02d}-01"
    last_month_end = f"{now.year}-{now.month:02d}-01"

    # Get all active credit card last4s
    cc_last4s = [r[0] for r in conn.execute(
        "SELECT last4 FROM cards WHERE active=1"
    ).fetchall()]

    if not cc_last4s:
        return {"current_month": {}, "last_month": {}}

    placeholders = ",".join(["?" for _ in cc_last4s])

    # Current month total spend (all cards)
    current_spend = conn.execute(
        f"SELECT COALESCE(SUM(amount), 0) as total, COUNT(*) as count "
        f"FROM transactions WHERE source IN ('card','sms') AND card_last4 IN ({placeholders}) "
        f"AND date >= ? AND txn_type='debit' AND is_duplicate=0",
        cc_last4s + [current_month_start]
    ).fetchone()

    # Last month total spend
    last_spend = conn.execute(
        f"SELECT COALESCE(SUM(amount), 0) as total, COUNT(*) as count "
        f"FROM transactions WHERE source IN ('card','sms') AND card_last4 IN ({placeholders}) "
        f"AND date >= ? AND date < ? AND txn_type='debit' AND is_duplicate=0",
        cc_last4s + [last_month_start, last_month_end]
    ).fetchone()

    # Statement totals for last month (statements whose billing period overlaps with last month)
    last_month_statements = conn.execute(
        f"SELECT COALESCE(SUM(s.total_amount), 0) as total, COUNT(*) as count "
        f"FROM statements s JOIN cards c ON s.card_id = c.id "
        f"WHERE c.active=1 AND s.month=? AND s.year=?",
        (prev_month, prev_year)
    ).fetchone()

    # Current month statements
    current_month_statements = conn.execute(
        f"SELECT COALESCE(SUM(s.total_amount), 0) as total, COUNT(*) as count "
        f"FROM statements s JOIN cards c ON s.card_id = c.id "
        f"WHERE c.active=1 AND s.month=? AND s.year=?",
        (now.month, now.year)
    ).fetchone()

    # Payments in current month
    current_month_paid = conn.execute(
        f"SELECT COALESCE(SUM(amount), 0) as total, COUNT(*) as count "
        f"FROM cc_payments WHERE card_last4 IN ({placeholders}) AND payment_date >= ?",
        cc_last4s + [current_month_start]
    ).fetchone()

    # Payments in last month
    last_month_paid = conn.execute(
        f"SELECT COALESCE(SUM(amount), 0) as total, COUNT(*) as count "
        f"FROM cc_payments WHERE card_last4 IN ({placeholders}) AND payment_date >= ? AND payment_date < ?",
        cc_last4s + [last_month_start, last_month_end]
    ).fetchone()

    return {
        "current_month": {
            "month": now.month,
            "year": now.year,
            "total_spent": round(current_spend[0], 2),
            "txn_count": current_spend[1],
            "statement_total": round(current_month_statements[0], 2),
            "statement_count": current_month_statements[1],
            "total_paid": round(current_month_paid[0], 2),
            "payment_count": current_month_paid[1],
        },
        "last_month": {
            "month": prev_month,
            "year": prev_year,
            "total_spent": round(last_spend[0], 2),
            "txn_count": last_spend[1],
            "statement_total": round(last_month_statements[0], 2),
            "statement_count": last_month_statements[1],
            "total_paid": round(last_month_paid[0], 2),
            "payment_count": last_month_paid[1],
        }
    }
