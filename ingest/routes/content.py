"""
Content query endpoint — unified view across all ingested data by content type.
Downstream apps use this to subscribe to the content types they need.
"""
from fastapi import APIRouter, Query
from typing import Optional

from db import get_db
from sources.base import ContentType
from sources.registry import list_sources, get_source

router = APIRouter()


@router.get("/sources")
def get_sources():
    """List all registered data sources with stats."""
    return {"sources": list_sources()}


@router.get("/types")
def get_content_types():
    """List all available content types."""
    return {"types": [{"value": ct.value, "name": ct.name} for ct in ContentType]}


@router.get("/query")
def query_content(
    content_type: Optional[str] = Query(None, alias="type"),
    source: Optional[str] = None,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    limit: int = 100,
    offset: int = 0,
):
    """
    Unified content query across all sources.
    Filters by content type and/or source.
    Currently queries the transactions table with content_type mapping.
    """
    conn = get_db()

    # Map content types to SQL filters
    type_filters = {
        "financial_transaction": "txn_type IN ('debit', 'credit') AND category NOT IN ('Bank Charges')",
        "financial_payment": "category = 'Bank Charges' OR raw_text LIKE '%PAYMENT%RECEIVED%'",
        "financial_statement": "source = 'card'",
        "receipt": "category IN ('Food & Dining', 'Shopping', 'Groceries')",
        "subscription": "category = 'Subscriptions'",
    }

    query = "SELECT * FROM transactions WHERE is_duplicate = 0"
    params = []

    if content_type and content_type in type_filters:
        query += f" AND ({type_filters[content_type]})"
    if source:
        query += " AND source = ?"
        params.append(source)
    if date_from:
        query += " AND date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND date <= ?"
        params.append(date_to)

    count_query = query.replace("SELECT *", "SELECT COUNT(*) as c", 1)
    total = conn.execute(count_query, params).fetchone()["c"]

    query += " ORDER BY date DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(query, params).fetchall()
    return {
        "total": total,
        "content_type": content_type,
        "source": source,
        "limit": limit,
        "offset": offset,
        "items": [dict(r) for r in rows],
    }


@router.get("/summary")
def content_summary(
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
):
    """Summary of content by source and category."""
    conn = get_db()

    where = "WHERE is_duplicate = 0"
    params = []
    if date_from:
        where += " AND date >= ?"
        params.append(date_from)
    if date_to:
        where += " AND date <= ?"
        params.append(date_to)

    by_source = conn.execute(
        f"SELECT source, COUNT(*) as count, "
        f"SUM(CASE WHEN txn_type='debit' THEN amount ELSE 0 END) as total_spend "
        f"FROM transactions {where} GROUP BY source ORDER BY total_spend DESC",
        params
    ).fetchall()

    by_category = conn.execute(
        f"SELECT category, COUNT(*) as count, "
        f"SUM(CASE WHEN txn_type='debit' THEN amount ELSE 0 END) as total_spend "
        f"FROM transactions {where} AND txn_type='debit' GROUP BY category ORDER BY total_spend DESC",
        params
    ).fetchall()

    return {
        "by_source": [dict(r) for r in by_source],
        "by_category": [dict(r) for r in by_category],
    }
