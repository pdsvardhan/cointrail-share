"""
Ingest service configuration.
All secrets via environment variables. No hardcoded passwords.
"""
import os

# Database
DB_PATH = os.environ.get("INGEST_DB", "/data/ingest.db")

# Card list + billing days (iter-18 M10): owner data, kept in the data dir, never in the repo.
CARDS_CONFIG_PATH = os.environ.get("CARDS_CONFIG_PATH", "/data/cards.json")

# Directories
STATEMENTS_DIR = os.environ.get("STATEMENTS_DIR", "/data/statements")

# Gmail OAuth
GMAIL_TOKEN_PATH = os.environ.get("GMAIL_TOKEN_PATH", "/data/gmail_tokens/token.json")
GMAIL_CREDS_PATH = os.environ.get("GMAIL_CREDS_PATH", "/data/gmail_tokens/credentials.json")
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# PDF passwords — loaded from env vars, keyed by bank
def get_pdf_passwords():
    """Build bank-to-password mapping from environment."""
    passwords = {}
    bank_vars = {
        "hdfc": "PDF_PASSWORD_HDFC",
        "icici": "PDF_PASSWORD_ICICI",
        "hsbc_cc": "PDF_PASSWORD_HSBC_CC",
        "hsbc_account": "PDF_PASSWORD_HSBC_ACCOUNT",
        "sbi": "PDF_PASSWORD_SBI",
        "axis": "PDF_PASSWORD_AXIS",
    }
    for bank, var in bank_vars.items():
        val = os.environ.get(var, "")
        if val:
            # Support multiple passwords separated by comma
            passwords[bank] = [p.strip() for p in val.split(",") if p.strip()]

    # NOTE: the legacy /data/pdf_passwords.txt fallback was removed (iter-13 #143).
    # All 6 bank passwords come from PDF_PASSWORD_* env vars (.env); the plaintext
    # file was world-readable inside the container mount and is no longer used.
    return passwords

# SMS backup safety net (iter-18 H7). The host script ops/ingest-sms-import.sh writes the name of
# the last imported phone backup here (/mnt/storage/sms is mounted read-only at /data/old_sms);
# /health warns when it goes stale. Path missing = backup not configured = no warning.
SMS_BACKUP_MARKER = os.environ.get("SMS_BACKUP_MARKER", "/data/old_sms/.last-imported")

# Server
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8600"))

# The API key (INGEST_API_KEY) is read by middleware/security.py at startup; it fails closed
# when the key is missing (iter-18 CH3).


def get_notify_settings():
    """Alert/digest channels for modules/notify.py (iter-18 HP6), read at call time so a
    `docker exec ingest python -m modules.notify` and the tests see the current environment.

    Each secret may be given directly or as a *_FILE path (a read-only mounted secret file), so
    the Telegram bot token never has to be copied into .env:
      TELEGRAM_BOT_TOKEN[_FILE] + TELEGRAM_CHAT_ID[_FILE]  -> Telegram Bot API sendMessage
      NTFY_URL (+ optional NTFY_TOKEN[_FILE])              -> ntfy topic, POST text
      INGEST_ALERT_WEBHOOK                                 -> generic webhook, POST text
    """
    def _secret(name):
        val = os.environ.get(name, "").strip()
        if val:
            return val
        path = os.environ.get(name + "_FILE", "").strip()
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    return f.read().strip()
            except OSError as e:
                print(f"[NOTIFY] cannot read {name}_FILE: {e.strerror}")
        return ""

    return {
        "telegram_bot_token": _secret("TELEGRAM_BOT_TOKEN"),
        "telegram_chat_ids": [c for c in _secret("TELEGRAM_CHAT_ID").replace(",", " ").split() if c],
        "ntfy_url": os.environ.get("NTFY_URL", "").strip(),
        "ntfy_token": _secret("NTFY_TOKEN"),
        "webhook_url": os.environ.get("INGEST_ALERT_WEBHOOK", "").strip(),
    }
