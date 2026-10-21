"""Email ingestion routes."""
import traceback
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from db import get_db

router = APIRouter()


@router.post("/sync")
def sync_email(query: Optional[str] = None):
    """Trigger Gmail sync."""
    try:
        from modules.email.gmail import sync_emails
        return sync_emails(query=query)
    except FileNotFoundError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e))


@router.get("/status")
def email_status():
    try:
        from modules.email.gmail import get_email_stats
        return get_email_stats()
    except Exception as e:
        return {"error": str(e)}


@router.get("/auth")
def email_auth_status():
    from modules.email.auth import check_auth
    return check_auth()


@router.get("/list")
def list_emails(
    sender: Optional[str] = None,
    sender_email: Optional[str] = None,
    is_financial: Optional[bool] = None,
    has_attachments: Optional[bool] = None,
    q: Optional[str] = None,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    limit: int = 50,
    offset: int = 0,
):
    """Query emails with filters. Used by downstream apps like BOTB for food analytics."""
    conn = get_db()
    query = "SELECT id, gmail_id, sender, sender_email, subject, body_snippet, body_text, date, has_attachments, is_financial FROM emails WHERE 1=1"
    params = []

    if sender:
        query += " AND sender_email LIKE ?"
        params.append(f"%{sender}%")
    if sender_email:
        query += " AND sender_email = ?"
        params.append(sender_email)
    if is_financial is not None:
        query += " AND is_financial = ?"
        params.append(1 if is_financial else 0)
    if has_attachments is not None:
        query += " AND has_attachments = ?"
        params.append(1 if has_attachments else 0)
    if q:
        query += " AND (subject LIKE ? OR body_snippet LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    if date_from:
        query += " AND date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND date <= ?"
        params.append(date_to)

    count_query = query.replace("SELECT id, gmail_id, sender, sender_email, subject, body_snippet, body_text, date, has_attachments, is_financial", "SELECT COUNT(*) as c", 1)
    total = conn.execute(count_query, params).fetchone()["c"]

    query += " ORDER BY date DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(query, params).fetchall()
    return {"total": total, "limit": limit, "offset": offset, "emails": [dict(r) for r in rows]}
