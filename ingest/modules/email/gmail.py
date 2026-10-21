"""
Gmail API client — fetches emails, stores metadata + body, downloads CC statement PDFs.
Handles nested MIME structures for body extraction and attachment discovery.
"""
import os
import re
import base64
import email.utils

from config import GMAIL_TOKEN_PATH, GMAIL_CREDS_PATH, GMAIL_SCOPES, STATEMENTS_DIR
from db import get_db, set_sync_state, get_sync_state
from modules.email.classifier import classify_email_financial


# ---------------------------------------------------------------------------
# Sender classification
# ---------------------------------------------------------------------------

CC_STATEMENT_SENDERS = {
    "creditcardstatement@mail.hsbc.co.in": "hsbc",
    "credit_cards@icicibank.com": "icici",
    "credit_cards@icici.bank.in": "icici",  # ICICI switched domains ~Apr 2026; statements froze until this was added (2026-07-14)
    "statements@sbicard.com": "sbi",
    "aurumcardstatement@sbicard.com": "sbi",
    "cc.statements@axisbank.com": "axis",
    "cc.statements@axis.bank.in": "axis",
    "alerts@hdfcbank.net": "hdfc",
    "creditcards@hdfcbank.net": "hdfc",
    "emailstatements.cards@hdfcbank.net": "hdfc",
    "emailstatements.cards@hdfcbank.bank.in": "hdfc",
}

SKIP_ATTACHMENT_SENDERS = {
    "hsbc@notification.hsbc.co.in",
    "statements@transactions.indmoney.com",
    "noreply@zerodha.com",
    "noreply@zerodha.net",
}

# Senders whose HTML body we want to store for rich parsing
AMAZON_SENDERS = {"amazon.in", "amazon.com"}


# SMS Backup & Restore — auto-import from emailed XML attachments
SMS_BACKUP_SENDERS = {s.strip().lower() for s in os.environ.get("SMS_BACKUP_SENDERS", "").split(",") if s.strip()}
SMS_BACKUP_DIR = "/data/sms-backups"
SMS_BACKUP_INDICATORS = {"sms", "backup", "messages"}


# ---------------------------------------------------------------------------
# Auth helpers (unchanged)
# ---------------------------------------------------------------------------

def get_gmail_service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    if not os.path.exists(GMAIL_TOKEN_PATH):
        raise FileNotFoundError(f"Gmail token not found at {GMAIL_TOKEN_PATH}. Run auth setup first.")

    creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_PATH, GMAIL_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(GMAIL_TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def extract_email_address(header_value):
    """Extract email from 'Name <email@example.com>' format."""
    if not header_value:
        return ""
    parsed = email.utils.parseaddr(header_value)
    return parsed[1] if parsed[1] else header_value


def _is_amazon_sender(sender_email):
    """Check if sender is from Amazon (amazon.in or amazon.com domain)."""
    if not sender_email:
        return False
    for domain in AMAZON_SENDERS:
        if domain in sender_email.lower():
            return True
    return False


# ---------------------------------------------------------------------------
# MIME helpers
# ---------------------------------------------------------------------------

def _walk_parts(payload):
    """Recursively walk MIME parts, return flat list of all leaf parts."""
    parts = []
    if "parts" in payload:
        for part in payload["parts"]:
            parts.extend(_walk_parts(part))
    else:
        parts.append(payload)
    return parts


def _extract_body_text(payload):
    """
    Extract readable body text from a Gmail message payload.
    Prefers text/plain; falls back to text/html with tag stripping.
    Returns at most 5000 characters.
    """
    all_parts = _walk_parts(payload)

    plain_text = ""
    html_text = ""

    for part in all_parts:
        mime = part.get("mimeType", "")
        body_data = part.get("body", {}).get("data")
        if not body_data:
            continue

        try:
            decoded = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
        except Exception:
            continue

        if mime == "text/plain" and not plain_text:
            plain_text = decoded
        elif mime == "text/html" and not html_text:
            html_text = decoded

    if plain_text:
        return plain_text[:5000]

    if html_text:
        # Strip HTML tags for a rough plain-text version
        text = re.sub(r"<style[^>]*>.*?</style>", "", html_text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:5000]

    return ""


def _extract_body_html(payload):
    """
    Extract raw HTML body from a Gmail message payload.
    Returns the first text/html part found, or empty string.
    Capped at 100KB to avoid bloating the DB.
    """
    all_parts = _walk_parts(payload)

    for part in all_parts:
        mime = part.get("mimeType", "")
        body_data = part.get("body", {}).get("data")
        if not body_data:
            continue

        if mime == "text/html":
            try:
                decoded = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
                return decoded[:100000]  # Cap at 100KB
            except Exception:
                continue

    return ""


def _find_pdf_attachments(payload, msg_id, service):
    """
    Walk all MIME parts, find PDF attachments, download via Gmail API.
    Returns list of (filename, bytes_data).
    """
    all_parts = _walk_parts(payload)
    results = []

    for part in all_parts:
        filename = part.get("filename", "")
        if not filename:
            continue
        if not filename.lower().endswith(".pdf"):
            continue

        att_id = part.get("body", {}).get("attachmentId")
        if not att_id:
            continue

        try:
            attachment = service.users().messages().attachments().get(
                userId="me", messageId=msg_id, id=att_id
            ).execute()
            data = base64.urlsafe_b64decode(attachment["data"])
            results.append((filename, data))
        except Exception as e:
            print(f"    Error downloading attachment {filename}: {e}")

    return results


def _extract_password_from_body(body_text):
    """
    Search body text for password hints commonly found in CC statement emails.
    Returns the password string or None.
    """
    if not body_text:
        return None

    # Common patterns in Indian bank CC statement emails
    patterns = [
        r"[Pp]assword\s*(?:is|:)\s*(\S+)",
        r"[Pp]assword\s*[-\u2013]\s*(\S+)",
        r"[Pp]assword\s+to\s+open\s*(?:is|:)?\s*(\S+)",
        r"[Pp]rotected\s+(?:with|by)\s+(?:password\s*)?(?:is|:)?\s*(\S+)",
        r"[Dd][Oo][Bb]\s*(?:in\s+)?(?:DDMMYYYY|ddmmyyyy)\s*(?:format\s*)?(?:is|:)?\s*(\S+)",
    ]

    for pat in patterns:
        m = re.search(pat, body_text)
        if m:
            pw = m.group(1).strip().rstrip(".")
            if len(pw) >= 4:
                return pw

    return None


def _save_pdf(filename, data):
    """Save PDF to /data/statements/ with dedup naming. Returns saved path."""
    os.makedirs(STATEMENTS_DIR, exist_ok=True)
    out_path = os.path.join(STATEMENTS_DIR, filename)
    counter = 1
    while os.path.exists(out_path):
        name, ext = os.path.splitext(filename)
        out_path = os.path.join(STATEMENTS_DIR, f"{name}_{counter}{ext}")
        counter += 1

    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


def _find_xml_attachments(payload, msg_id, service):
    """
    Walk all MIME parts, find XML attachments, download via Gmail API.
    Returns list of (filename, bytes_data).
    """
    all_parts = _walk_parts(payload)
    results = []

    for part in all_parts:
        filename = part.get("filename", "")
        if not filename:
            continue
        if not filename.lower().endswith(".xml"):
            continue

        att_id = part.get("body", {}).get("attachmentId")
        if not att_id:
            continue

        try:
            attachment = service.users().messages().attachments().get(
                userId="me", messageId=msg_id, id=att_id
            ).execute()
            data = base64.urlsafe_b64decode(attachment["data"])
            results.append((filename, data))
        except Exception as e:
            print(f"    Error downloading XML attachment {filename}: {e}")

    return results


def _is_sms_backup(sender_email, subject, filename):
    """Check if an XML attachment looks like an SMS backup."""
    if sender_email not in SMS_BACKUP_SENDERS:
        return False
    check_text = (filename + " " + subject).lower()
    return any(ind in check_text for ind in SMS_BACKUP_INDICATORS)


def _process_sms_backup(xml_data, filename):
    """
    Save SMS backup XML to disk, run the importer, then process pending
    financial SMS into transactions. Returns dict with results.
    """
    os.makedirs(SMS_BACKUP_DIR, exist_ok=True)

    # Save file with dedup naming
    out_path = os.path.join(SMS_BACKUP_DIR, filename)
    counter = 1
    base, ext = os.path.splitext(filename)
    while os.path.exists(out_path):
        out_path = os.path.join(SMS_BACKUP_DIR, f"{base}_{counter}{ext}")
        counter += 1

    with open(out_path, "wb") as f:
        f.write(xml_data)
    print(f"  SMS backup saved: {out_path} ({len(xml_data)} bytes)")

    # Import SMS from XML
    from modules.sms.xml_import import process_xml_file
    import_result = process_xml_file(out_path)
    print(f"  SMS import result: {import_result}")

    # Process pending financial SMS into transactions — the same process_sms_row path as realtime
    # /sms and the hourly pass (iter-18 HP1). Its private INSERT loop deduped on amount+date+description
    # (dropping genuine repeat payments, M4) and swallowed every insert error silently; backup copies
    # of already-delivered SMS are now linked by modules.merge instead.
    from modules.sms.processing import process_pending

    pending_result = process_pending(get_db(), limit=5000)
    txn_count = pending_result["processed"]
    if pending_result["total_pending"]:
        print(f"  SMS process-pending: {txn_count} transactions from {pending_result['total_pending']} pending "
              f"{pending_result['by_class']}")
    else:
        print("  SMS process-pending: no pending SMS")

    return {
        "file": out_path,
        "import": import_result,
        "transactions_created": txn_count,
    }


# ---------------------------------------------------------------------------
# Per-message processing
# ---------------------------------------------------------------------------

def _process_single_message(msg_id, service, conn, stats):
    """
    Process one email:
      1. Metadata fetch to classify
      2. If financial, full fetch for body + attachments
      3. Store in DB; download PDFs only from CC_STATEMENT_SENDERS
    """
    # --- Phase 1: metadata fetch for classification ---
    try:
        msg = service.users().messages().get(
            userId="me", id=msg_id, format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"]
        ).execute()
    except Exception as e:
        print(f"  Error fetching metadata {msg_id}: {e}")
        return

    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    sender = headers.get("from", "")
    sender_email = extract_email_address(sender).lower()
    recipient = headers.get("to", "")
    subject = headers.get("subject", "")
    date = headers.get("date", "")
    snippet = msg.get("snippet", "")
    labels = ",".join(msg.get("labelIds", []))
    thread_id = msg.get("threadId", "")

    sender_email = sender_email.lower()  # Normalize for consistent matching
    is_financial = 1 if classify_email_financial(sender_email, subject, snippet) else 0

    # --- Phase 2: if financial, re-fetch full to get body + attachments ---
    body_text = ""
    body_html = ""
    pdf_attachments = []  # list of (filename, bytes)
    has_attachments = 0

    if is_financial:
        try:
            full_msg = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()
            payload = full_msg.get("payload", {})
        except Exception as e:
            print(f"  Error fetching full message {msg_id}: {e}")
            payload = msg.get("payload", {})

        body_text = _extract_body_text(payload)

        # Extract HTML body for Amazon emails (for rich product parsing)
        if _is_amazon_sender(sender_email):
            body_html = _extract_body_html(payload)

        # Check all parts for any attachments (for has_attachments flag)
        all_parts = _walk_parts(payload)
        for part in all_parts:
            if part.get("filename") and part.get("body", {}).get("attachmentId"):
                has_attachments = 1
                break

        # Only download PDFs from known CC statement senders (not skip senders)
        if sender_email in CC_STATEMENT_SENDERS and sender_email not in SKIP_ATTACHMENT_SENDERS:
            pdf_attachments = _find_pdf_attachments(payload, msg_id, service)
    else:
        # Non-financial: just check metadata for attachment flag
        payload = msg.get("payload", {})
        parts = payload.get("parts", [])
        for part in parts:
            if part.get("filename") and part.get("body", {}).get("attachmentId"):
                has_attachments = 1
                break

    # --- Phase 3: store in DB ---
    try:
        cur = conn.execute(
            "INSERT INTO emails (gmail_id, thread_id, sender, sender_email, recipient, subject, "
            "body_snippet, date, labels, has_attachments, is_financial, body_html) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (msg_id, thread_id, sender, sender_email, recipient, subject,
             body_text if body_text else snippet[:500], date, labels, has_attachments, is_financial,
             body_html if body_html else None)
        )
        email_db_id = cur.lastrowid
        stats["stored"] += 1
        if is_financial:
            stats["financial"] += 1
    except Exception as e:
        print(f"  Error storing {msg_id}: {e}")
        return

    # --- Phase 4: save PDF attachments to disk + DB ---
    for filename, data in pdf_attachments:
        try:
            out_path = _save_pdf(filename, data)
            conn.execute(
                "INSERT INTO email_attachments (email_id, gmail_message_id, attachment_id, "
                "filename, mime_type, file_path, size_bytes) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (email_db_id, msg_id, "", filename, "application/pdf", out_path, len(data))
            )
            stats["attachments"] += 1
            bank = CC_STATEMENT_SENDERS.get(sender_email, "unknown")
            print(f"  Saved PDF: {filename} ({len(data)} bytes) [bank={bank}]")
        except Exception as e:
            print(f"  Error saving attachment {filename}: {e}")

    # --- Phase 5: detect & import SMS backup XML attachments ---
    if sender_email in SMS_BACKUP_SENDERS:
        try:
            # Re-fetch full message if we didn't already (non-financial emails skip full fetch)
            if not is_financial:
                full_msg = service.users().messages().get(
                    userId="me", id=msg_id, format="full"
                ).execute()
                payload = full_msg.get("payload", {})
        except Exception as e:
            print(f"  Error fetching full message for SMS backup check {msg_id}: {e}")

        xml_attachments = _find_xml_attachments(payload, msg_id, service)
        for xml_filename, xml_data in xml_attachments:
            if _is_sms_backup(sender_email, subject, xml_filename):
                try:
                    result = _process_sms_backup(xml_data, xml_filename)
                    stats["sms_backups"] += 1
                    print(f"  SMS backup imported: {xml_filename} -> {result['import']}")
                except Exception as e:
                    print(f"  Error processing SMS backup {xml_filename}: {e}")

    conn.commit()


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------

def sync_emails(max_results=500, query=None):
    """
    Paginated email sync.  Fetches all new emails since last sync,
    classifies them, extracts body text for financial emails, and
    downloads CC statement PDFs from known senders.
    """
    conn = get_db()
    service = get_gmail_service()

    if query is None:
        query = "newer_than:6m"

    print(f"Syncing emails with query: {query}")

    results = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()

    messages = results.get("messages", [])
    print(f"Found {len(messages)} messages to process")

    stats = {"total": 0, "stored": 0, "financial": 0, "attachments": 0, "skipped": 0, "sms_backups": 0}
    newest_id = None

    def _process_batch(message_list):
        nonlocal newest_id
        for msg_meta in message_list:
            stats["total"] += 1
            msg_id = msg_meta["id"]

            if newest_id is None:
                newest_id = msg_id

            # Skip if already synced
            existing = conn.execute(
                "SELECT id FROM emails WHERE gmail_id = ?", (msg_id,)
            ).fetchone()
            if existing:
                stats["skipped"] += 1
                continue

            _process_single_message(msg_id, service, conn, stats)

            if stats["total"] % 50 == 0:
                print(f"  Processed {stats['total']} emails...")

    _process_batch(messages)

    # Handle pagination
    while "nextPageToken" in results:
        page_token = results["nextPageToken"]
        results = service.users().messages().list(
            userId="me", q=query, maxResults=max_results, pageToken=page_token
        ).execute()
        new_messages = results.get("messages", [])
        print(f"  Next page: {len(new_messages)} more messages")
        _process_batch(new_messages)

    if newest_id:
        set_sync_state("last_email_sync_id", newest_id)

    print(f"Email sync complete: {stats}")
    return stats


def backfill_amazon_html():
    """
    Re-fetch HTML bodies for existing Amazon emails that don't have body_html yet.
    Updates the body_html column in-place without re-inserting.
    """
    conn = get_db()
    service = get_gmail_service()

    rows = conn.execute(
        "SELECT id, gmail_id, sender_email FROM emails "
        "WHERE (sender_email LIKE '%amazon.in%' OR sender_email LIKE '%amazon.com%') "
        "AND (body_html IS NULL OR body_html = '') "
        "AND is_financial = 1"
    ).fetchall()

    print(f"Backfilling HTML for {len(rows)} Amazon emails...")
    updated = 0

    for row in rows:
        try:
            full_msg = service.users().messages().get(
                userId="me", id=row["gmail_id"], format="full"
            ).execute()
            payload = full_msg.get("payload", {})
            html = _extract_body_html(payload)
            if html:
                conn.execute(
                    "UPDATE emails SET body_html = ? WHERE id = ?",
                    (html, row["id"])
                )
                updated += 1
                if updated % 10 == 0:
                    conn.commit()
                    print(f"  Updated {updated} emails...")
        except Exception as e:
            print(f"  Error backfilling {row['gmail_id']}: {e}")

    conn.commit()
    print(f"Backfill complete: {updated}/{len(rows)} Amazon emails updated with HTML")
    return {"total": len(rows), "updated": updated}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def get_email_stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM emails").fetchone()["c"]
    financial = conn.execute("SELECT COUNT(*) as c FROM emails WHERE is_financial = 1").fetchone()["c"]
    with_attachments = conn.execute("SELECT COUNT(*) as c FROM emails WHERE has_attachments = 1").fetchone()["c"]
    attachments_downloaded = conn.execute("SELECT COUNT(*) as c FROM email_attachments").fetchone()["c"]
    last_sync = get_sync_state("last_email_sync_id")
    sms_backups = len([f for f in os.listdir(SMS_BACKUP_DIR) if f.endswith(".xml")]) if os.path.exists(SMS_BACKUP_DIR) else 0
    return {
        "total_emails": total,
        "financial": financial,
        "with_attachments": with_attachments,
        "pdf_attachments_downloaded": attachments_downloaded,
        "sms_backups_imported": sms_backups,
        "last_sync_id": last_sync,
    }
