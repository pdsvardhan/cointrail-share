"""PDF CC statement routes.

Parsing and storing live in modules/pdf/store.py (iter-18): every line goes through
txn_store.insert_transaction with source_ref '<file>#<line>', and the statement's printed Total
Amount Due / Payment Due Date / Minimum Due go to cards_service.record_bank_statement.
"""
import os
import tempfile
from fastapi import APIRouter, UploadFile, File, HTTPException

from config import STATEMENTS_DIR
from db import get_db

router = APIRouter()


def _parse_and_store(pdf_path, conn):
    """Detect bank, parse the statement, store its lines and record its bill. Returns result dict."""
    from modules.pdf.store import parse_and_store
    return parse_and_store(pdf_path, conn)


def _mark_attachment_parsed(conn, pdf_path):
    """Mark the email_attachment record as parsed for this PDF file path."""
    conn.execute(
        "UPDATE email_attachments SET is_parsed = 1, parsed_at = datetime('now') "
        "WHERE file_path = ? AND is_parsed = 0",
        (pdf_path,)
    )


@router.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    """Upload and parse a CC statement PDF."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    try:
        conn = get_db()
        result = _parse_and_store(tmp_path, conn)
        conn.commit()
        return result
    finally:
        os.unlink(tmp_path)


@router.post("/parse-path")
def parse_pdf_path(data: dict):
    """Parse a PDF already on disk."""
    path = data.get("path", "")
    if not os.path.exists(path):
        raise HTTPException(404, f"File not found: {path}")
    conn = get_db()
    result = _parse_and_store(path, conn)
    _mark_attachment_parsed(conn, path)
    conn.commit()
    return result


@router.post("/process-all")
def process_all_pdfs():
    """Parse all PDFs in the statements directory and update attachment tracking.

    Safe to repeat: lines already stored (same file, a re-downloaded copy, or a pre-iter-18
    parse) are recognised and not inserted again — see modules/pdf/store.py."""
    from modules.pdf.store import statement_files

    conn = get_db()
    results = []
    for pdf_path in statement_files(STATEMENTS_DIR):
        result = _parse_and_store(pdf_path, conn)
        # Bridge: mark corresponding email_attachment as parsed
        _mark_attachment_parsed(conn, pdf_path)
        results.append(result)
    conn.commit()
    return {"processed": len(results), "results": results}


@router.post("/process-unparsed")
def process_unparsed_pdfs():
    """Process only PDFs that are tracked as unparsed in email_attachments."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, file_path, filename FROM email_attachments WHERE is_parsed = 0"
    ).fetchall()

    results = []
    for row in rows:
        file_path = row["file_path"]
        if not file_path or not os.path.exists(file_path):
            results.append({"file": row["filename"], "status": "file_not_found", "path": file_path})
            continue

        result = _parse_and_store(file_path, conn)
        status = result.get("status", "ok")

        # Mark as parsed if successfully processed or deliberately skipped
        if status in ("skipped",) or result.get("bank"):
            conn.execute(
                "UPDATE email_attachments SET is_parsed = 1, parsed_at = datetime('now') WHERE id = ?",
                (row["id"],)
            )

        results.append(result)

    conn.commit()
    return {"processed": len(results), "results": results}


@router.get("/status")
def pdf_status():
    from modules.pdf.decrypt import is_encrypted
    from modules.pdf.store import statement_files
    pdfs = statement_files(STATEMENTS_DIR)
    encrypted = sum(1 for p in pdfs if is_encrypted(p))

    conn = get_db()
    unparsed = conn.execute("SELECT COUNT(*) as c FROM email_attachments WHERE is_parsed = 0").fetchone()["c"]
    total_tracked = conn.execute("SELECT COUNT(*) as c FROM email_attachments").fetchone()["c"]

    return {
        "total_on_disk": len(pdfs),
        "encrypted": encrypted,
        "readable": len(pdfs) - encrypted,
        "tracked_attachments": total_tracked,
        "unparsed_attachments": unparsed,
    }
