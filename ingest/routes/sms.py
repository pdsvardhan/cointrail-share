"""SMS ingestion routes."""
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from db import get_db
from modules import quarantine
from modules.sms.receiver import store_sms
from modules.sms.parser import parse_sms_batch
from modules.sms.processing import process_sms_row, process_pending, retry_quarantine
from modules.categorizer import categorize
from util.timez import utc_naive_iso_from_epoch

router = APIRouter()


class SmsIngest(BaseModel):
    sender: Optional[str] = None
    body: Optional[str] = None
    timestamp: Optional[str] = None
    from_field: Optional[str] = Field(None, alias="from")
    text: Optional[str] = None
    sentStamp: Optional[int] = None
    receivedStamp: Optional[int] = None
    sim: Optional[str] = None
    model_config = {"populate_by_name": True}


def _log_receipt(data: SmsIngest, resp: dict):
    """iter-16 R4: append-only audit of every /sms POST — including rejects — so 'did the
    phone actually send it?' is answerable from the server, not inferred. Never raises:
    an audit failure must not break ingestion. Body is stored as a SHA-256 prefix, not
    plaintext."""
    try:
        import hashlib
        sender = data.sender or data.from_field or ""
        body = data.body or data.text or ""
        conn = get_db()
        conn.execute(
            "INSERT INTO sms_receipts (sender, body_sha, body_len, is_financial, status, sms_id, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sender[:60],
             hashlib.sha256(body.encode("utf-8", "ignore")).hexdigest()[:16],
             len(body),
             1 if resp.get("is_financial") else 0,
             resp.get("status"),
             resp.get("sms_id"),
             resp.get("reason") or resp.get("error") or resp.get("message")),
        )
        conn.commit()
    except Exception as e:
        print(f"[SMS] receipt-log failed (non-fatal): {e}")


@router.post("")
def receive_sms(data: SmsIngest):
    """Receive SMS from phone (SMS Forwarder). Logs a receipt for every delivery (R4),
    then stores + processes it through modules.sms.processing."""
    resp = _receive_sms_impl(data)
    _log_receipt(data, resp)
    return resp


def _phone_timestamp(raw_ts):
    """The forwarder's timestamp as the stored naive-UTC ISO string.

    Epoch milliseconds/seconds (int, or a string of digits) are converted with
    util.timez.utc_naive_iso_from_epoch — explicit UTC, the same format as before, but no longer
    dependent on the container's TZ (iter-18 M8). Anything else (an ISO string) is kept as sent."""
    if raw_ts is None:
        return None
    if isinstance(raw_ts, (int, float)) or (isinstance(raw_ts, str) and raw_ts.strip().isdigit()):
        try:
            return utc_naive_iso_from_epoch(raw_ts)
        except (ValueError, OverflowError, OSError):
            return None
    return raw_ts


def _receive_sms_impl(data: SmsIngest):
    """Store + process. Returns the response dict (also fed to the R4 audit)."""
    sms_sender = data.sender or data.from_field or ""
    sms_body = data.body or data.text or ""
    raw_ts = data.timestamp or data.receivedStamp or data.sentStamp

    # iter-16 D3: reject un-substituted forwarder template placeholders.
    # A misconfigured template sends the literal "{device_time_millis}" instead of a
    # value; those rows then look like real timestamps and quietly corrupt any
    # date-ordered query. Store NULL instead of a lie — store_sms falls back to
    # received_at, which is the server's own clock and always trustworthy.
    if isinstance(raw_ts, str) and ("{" in raw_ts or "%" in raw_ts):
        print(f"[SMS] Dropping template-literal timestamp: {raw_ts!r}")
        raw_ts = None
    sms_timestamp = _phone_timestamp(raw_ts)

    if not sms_sender or not sms_body:
        return {"status": "error", "message": "Missing sender/from and body/text fields"}

    result = store_sms(sms_sender, sms_body, phone_timestamp=sms_timestamp)
    if not result["is_financial"]:
        return {"status": "stored", "is_financial": False, "transaction": None, "sms_id": result["id"]}

    conn = get_db()
    row = conn.execute(
        "SELECT id, sender, body, phone_timestamp, received_at FROM sms WHERE id = ?", (result["id"],)
    ).fetchone()
    # iter-18 HP1: the realtime path runs the same classification as the batch and backup paths.
    # The forwarder retries (10x, exponential backoff); store_sms dedups the sms row and
    # insert_transaction is idempotent on the SMS id, so a re-POST never double-counts. A message
    # with no date in its body is dated by its IST receive day here too (no more NOT NULL failures).
    try:
        out = process_sms_row(conn, row)
    except Exception as e:
        # left pending: the hourly sms_process pass retries it and quarantines it if it fails again
        return {"status": "stored", "is_financial": True, "error": str(e), "sms_id": result["id"]}

    if out["class"] != "transaction":
        status = "quarantined" if out["class"] == "quarantined" else "stored"
        return {"status": status, "is_financial": True, "transaction": None,
                "reason": out["class"], "sms_id": result["id"]}

    txn = out["txn"]
    if out.get("status") == "exists":
        return {"status": "duplicate", "is_financial": True, "transaction": None, "sms_id": result["id"]}
    return {
        "status": "processed",
        "is_financial": True,
        "sms_id": result["id"],
        "transaction": {
            "id": out.get("transaction_id"),
            "amount": txn["amount"],
            "currency": txn["currency"],
            "merchant": out.get("merchant"),
            "category": out.get("category"),
            "kind": out.get("kind"),
            "date": txn["date"],
            "confidence": out.get("confidence"),
        }
    }


@router.post("/parse")
def parse_sms_manual(data: dict):
    """Parse SMS text manually (paste and parse)."""
    messages = data.get("messages", [])
    if isinstance(messages, str):
        messages = [m.strip() for m in messages.split("\n") if m.strip()]
    results = parse_sms_batch(messages)
    enriched = []
    for r in results:
        merchant, category, confidence = categorize(r["description"], context=r["source"])
        r["merchant"] = merchant
        r["category"] = category
        r["confidence"] = confidence
        enriched.append(r)
    return {"parsed": len(enriched), "transactions": enriched}


@router.post("/process-pending")
def process_pending_sms():
    """Process all unprocessed financial SMS (iter-18 HP1: through process_sms_row)."""
    res = process_pending(get_db(), limit=500)
    if not res["total_pending"]:
        res["message"] = "No pending SMS"
    return res


@router.get("/quarantine")
def sms_quarantine():
    """Financial messages no rule could read, grouped by bank + template (iter-18 HP1)."""
    conn = get_db()
    sms_open = conn.execute(
        "SELECT COUNT(*) c FROM parse_quarantine WHERE source = 'sms' AND resolved_at IS NULL"
    ).fetchone()["c"]
    return {"sms_open": sms_open, "summary": quarantine.summary(conn, source="sms")}


@router.post("/quarantine/retry")
def sms_quarantine_retry():
    """Re-run every open SMS quarantine entry — call after adding a pattern."""
    return retry_quarantine(get_db())


@router.post("/import/xml")
def import_sms_xml(data: dict):
    """Import SMS from XML file on disk."""
    path = data.get("path", "")
    if not os.path.exists(path):
        raise HTTPException(404, f"File not found: {path}")
    from modules.sms.xml_import import process_xml_file
    return process_xml_file(path)
