# Ingest Service — Architecture Overhaul Plan

**Date:** 2026-04-01
**Status:** DRAFT — Awaiting session execution
**Scope:** Full restructure of the Ingest service — security, modularity, data quality, extensibility

---

## Vision Recap

Ingest is the **central data hub** for all personal data ingestion. It receives raw data from multiple sources (SMS, email, future: notifications, chat/media), processes and classifies it, stores it in a unified schema, and serves it to downstream service suites via API.

```
INPUT SOURCES
SMS | Email/Gmail | [Future: Notif, Chat, Media]
  |        |
  v        v
INGEST SERVICE
  Receivers -> Processors -> Unified Store (SQLite + queue)
                                  |
                              REST API (query layer)
                                  |
              +---------+---------+---------+
              v         v                   v
    PF Suite (Cointrail)   Future Suite 2    Future Suite 3
    - Expenses
    - CC Statements
    - Food Analytics
    - [Future apps]
```

**Key principle:** Ingest stores raw + processed data. Analytics belong in downstream suites. Ingest provides the data, not the insights.

---

## Phase 0: Project Setup and Structure

### 0.1 New directory structure

```
/app/
  main.py                    # FastAPI app (slim: startup, middleware, router mounts)
  config.py                  # Env-based config (no hardcoded secrets)
  db.py                      # Database connection, schema, migrations

  routes/
    __init__.py
    sms.py                   # SMS endpoints
    email.py                 # Email endpoints
    pdf.py                   # PDF endpoints
    transactions.py          # Transaction query/update endpoints
    cards.py                 # Card + statement + payment endpoints
    system.py                # Health, stats, cron, aliases, enrichments

  services/
    __init__.py
    sms_service.py           # SMS ingestion logic (receive, parse, store)
    email_service.py         # Email sync + attachment download
    pdf_service.py           # PDF detection, decryption, parsing, storing
    categorizer.py           # Merchant alias lookup + keyword fallback + heuristics
    cross_matcher.py         # SMS vs CC dedup/enrichment
    enrichment.py            # Email receipt to transaction linking
    queue.py                 # Background task queue (SQLite-based)
    recurring.py             # Recurring expense detection
    cards_service.py         # Statement backfill, payment parsing, billing

  sources/
    __init__.py
    base.py                  # Abstract base class for input sources
    sms/
      __init__.py
      receiver.py            # SMS storage + classification
      parser.py              # SMS regex patterns
      xml_import.py          # Bulk XML import
    email/
      __init__.py
      gmail.py               # Gmail API client
      classifier.py          # Email financial classification
      auth.py                # OAuth2 management
    pdf/
      __init__.py
      decrypt.py             # PDF decryption with bank-aware password mapping
      detect.py              # Bank detection from PDF content
      parsers/
        __init__.py
        base.py              # Transaction dataclass + StatementParser protocol
        hdfc.py, hsbc.py, icici.py, axis.py, sbi.py

  middleware/
    __init__.py
    auth.py                  # API key authentication middleware
    security.py              # CORS config, rate limiting
```

### 0.2 Migration strategy

- Create new structure alongside old code
- Move file by file, verifying endpoints after each move
- main.py becomes a thin shell that mounts routers
- No endpoint URL changes — Cointrail does not break

---

## Phase 1: Security Hardening

### 1.1 CORS lockdown
- Replace allow_origins=["*"] with explicit origins:
  - https://cointrail.example.com
  - http://cointrail:8501 (Docker internal)
  - http://localhost:3000 (local dev)
- Allow configuring via env var ALLOWED_ORIGINS

### 1.2 API authentication
- Add API key middleware: X-API-Key header
- Generate a random API key, store in env var INGEST_API_KEY
- Health endpoint exempted from auth
- Cointrail passes the key in all requests to Ingest

### 1.3 Secrets management
- Move PDF passwords to env vars: PDF_PASSWORD_HDFC, PDF_PASSWORD_ICICI, etc.
- Gmail OAuth tokens stay on disk (required by Google lib) but ensure /data/gmail_tokens/ is not exposed
- Remove pdf_passwords.txt flat file
- Remove hardcoded passwords from config.py
- Add .env file (gitignored) for local/Docker secrets
- docker-compose.yml uses env_file directive

### 1.4 Input validation
- Validate all incoming SMS payloads (sender length, body size limits)
- Validate file uploads (PDF only, max 10MB)
- Sanitize search/filter query params (already parameterized, but audit)

---

## Phase 2: Modular Restructure

### 2.1 Route extraction
Extract from main.py into routes/:
- routes/sms.py — POST /sms, POST /sms/parse, POST /sms/process-pending, POST /sms/import/xml
- routes/email.py — POST /email/sync, GET /email/status, GET /email/auth
- routes/pdf.py — POST /pdf/upload, POST /pdf/parse-path, POST /pdf/process-all, GET /pdf/status
- routes/transactions.py — GET /transactions, PUT /transactions/{id}, POST /recategorize-all, GET /recurring/detect
- routes/cards.py — GET /cards, GET /cards/summary, GET /cards/{id}/statements, GET /cards/{id}/transactions, POST /statements/backfill, POST /statements/from-sms, POST /payments/backfill, POST /payments/dedup
- routes/system.py — GET /health, GET /stats, GET /aliases, POST /aliases, GET /cron/status, POST /cron/trigger, GET /enrichments, POST /enrichment/run, UPI endpoints

### 2.2 Service layer extraction
Business logic moves out of route handlers into services:
- CC payment SMS parsing (main.py lines 996-1073) -> services/cards_service.py
- Statement backfill logic (lines 1125-1190) -> services/cards_service.py
- Payment backfill logic (lines 1077-1122) -> services/cards_service.py
- Statement-from-SMS logic (lines 1346-1484) -> services/cards_service.py
- Recurring detection (lines 727-893) -> services/recurring.py

### 2.3 Food module removal
- Remove modules/food/ entirely from Ingest
- Food analytics (order parsing, restaurant extraction, cross-matching) moves to Cointrail
- Cointrail already has access to emails and transactions via Ingest API
- Cointrail can call GET /transactions and GET /emails (new endpoint) to build food analytics itself
- Add a new GET /emails query endpoint to Ingest so Cointrail can fetch email data

### 2.4 Main.py becomes thin (~80 lines)
```python
# main.py — just wiring
from fastapi import FastAPI
from middleware.security import setup_cors, setup_auth
from routes import sms, email, pdf, transactions, cards, system

app = FastAPI(title="Ingest")
setup_cors(app)
setup_auth(app)

app.include_router(sms.router, prefix="/sms", tags=["SMS"])
app.include_router(email.router, prefix="/email", tags=["Email"])
app.include_router(pdf.router, prefix="/pdf", tags=["PDF"])
app.include_router(transactions.router, tags=["Transactions"])
app.include_router(cards.router, prefix="/cards", tags=["Cards"])
app.include_router(system.router, tags=["System"])

@app.on_event("startup")
def startup():
    # DB init, migrations, scheduler start
    ...
```

---

## Phase 3: Background Task Queue

### 3.1 SQLite-based task queue
No external dependencies (no Redis, no Celery). Simple, fits the single-user model.

```sql
CREATE TABLE task_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    payload TEXT,
    result TEXT,
    error TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    completed_at TEXT,
    retry_count INTEGER DEFAULT 0
);
```

task_type values: email_sync, pdf_process, sms_process, cross_match, enrichment

### 3.2 Task runner
- Background thread polls task_queue every 5 seconds
- Picks up pending tasks in FIFO order
- Executes the corresponding service function
- Updates status to completed or failed
- Max 3 retries for failed tasks

### 3.3 API changes
- POST /email/sync -> enqueues task, returns {"task_id": 123, "status": "queued"}
- POST /pdf/process-all -> enqueues task
- GET /tasks/{id} -> check task status
- GET /tasks -> list recent tasks
- POST /cron/trigger -> enqueues all sync tasks

### 3.4 Hourly cron
Instead of running sync directly, cron now enqueues:
1. Email sync task
2. SMS process-pending task
3. Cross-match task
4. Enrichment task

Each runs sequentially via the queue, with proper error handling and status tracking.

---

## Phase 4: Data Quality Fixes

### 4.1 PDF password resolution
- Build a bank-to-password mapping in config (from env vars)
- decrypt.py tries passwords in order: bank-specific first, then common passwords
- After decryption, re-run bank detection and parsing
- Log which PDFs could not be decrypted (with bank if detectable)
- Endpoint: GET /pdf/unresolved — list PDFs that need manual password entry

### 4.2 CC date normalization
- Ensure ALL parsers output YYYY-MM-DD (some still output DD/MM/YYYY)
- Run migration to fix existing DD/MM/YYYY dates in transactions table
- Add date validation in the Transaction dataclass __post_init__

### 4.3 SMS parser coverage improvement
- Audit the 116 unprocessed financial SMS
- Group by sender pattern to identify missing regex patterns
- Add patterns for: SBI debit/credit, Kotak, IndusInd, Axis debit
- Add a catch-all pattern for UPI transactions (amount + VPA)
- Endpoint: GET /sms/unmatched — show financial SMS that could not be parsed (for debugging)

### 4.4 Cross-matcher improvements
- Fix: ensure both SMS and CC dates are YYYY-MM-DD before matching
- Add fuzzy amount matching (+-2% for foreign currency rounding)
- Add merchant name similarity as a matching signal (not just amount + date)
- Log match confidence scores

### 4.5 Categorization improvements
- Space-collapsed matching already in Pass 1.5 — verify it works
- Add case-insensitive UPI VPA matching
- Normalize merchant descriptions before categorization (strip PYU*, PAY*, etc.)

---

## Phase 5: Extensibility — Future Source Abstraction

### 5.1 Source interface
```python
class DataSource(ABC):
    source_name: str           # sms, email, notification, chat

    @abstractmethod
    def sync(self, **kwargs) -> dict:
        """Pull new data from this source."""
        pass

    @abstractmethod
    def classify(self, raw_data) -> bool:
        """Is this data relevant (financial, actionable)?"""
        pass

    @abstractmethod
    def process(self, raw_data) -> list:
        """Parse raw data into transactions."""
        pass
```

### 5.2 Source registry
```python
SOURCES = {
    "sms": SmsSource(),
    "email": EmailSource(),
    # Future:
    # "notification": NotificationSource(),
    # "telegram": TelegramSource(),
}
```

### 5.3 Content type system
Instead of just "financial", classify content into types:
- financial_transaction — UPI, card spend, bank transfer
- financial_statement — CC statement email/PDF
- financial_payment — CC payment confirmation
- receipt — order confirmation, delivery receipt
- subscription — subscription renewal/charge
- informational — account alerts, balance updates
- promotional — offers, marketing (skip/delete)
- unknown — needs manual classification

This allows future downstream suites to subscribe to the content types they need.

---

## Phase 6: New API Endpoints for Downstream

### 6.1 Email query endpoint (for Cointrail food analytics)
```
GET /emails?sender=swiggy&is_financial=true&from=2026-01-01&to=2026-03-31
```
Returns email metadata + body text for downstream parsing.

### 6.2 Content type query
```
GET /content?type=financial_transaction&source=sms&from=2026-03-01
```
Unified query across all sources.

### 6.3 Webhook/callback registration (future)
```
POST /webhooks
{
  "url": "http://botb:8501/api/ingest-callback",
  "content_types": ["financial_transaction", "receipt"],
  "events": ["new_transaction"]
}
```
So downstream apps get notified in real-time instead of polling.

---

## Phase 7: SMS Solution (Parked — Last Action)

### Options to evaluate:
1. **Tasker selective forwarding** — Tasker can filter SMS by sender regex and POST only financial ones. Does not require SMS default app permissions. Less likely to conflict with banking apps.
2. **Periodic XML sync** — Use SMS Backup and Restore app, set auto-backup to Google Drive or server, Ingest polls for new backups.
3. **ADB over WiFi** — Script on server runs adb pull periodically to extract SMS database.
4. **MacroDroid or Automate** — Alternative automation apps that may not trigger banking app security.

### Recommendation:
Tasker selective forwarding is the safest bet — it does not replace the default SMS app, does not interfere with RCS/banking, and gives precise control. But test it first.

---

## Future Items (Documented, Not Planned)

### Telegram/Chat Interface — TODO
- Telegram bot that receives messages, voice notes, images
- Voice notes -> speech-to-text -> classify and store
- Images -> OCR for receipts -> classify and store
- Manual text messages -> classify and store
- Would be a new source in the SOURCES registry

### Notification Forwarding
- Android notification listener service
- Forwards app notifications to Ingest
- Would capture: payment confirmations, delivery updates, reminders
- New source: NotificationSource

### Future Downstream Suites
- Task tracker / To-do (consumes notifications + chat messages)
- Media library (consumes forwarded images, voice notes)
- Health tracker (consumes health app notifications)

---

## Execution Order

**Session 1 (Current plan creation):** DONE

**Session 2: Security + Restructure Foundation**
1. Create new directory structure (routes/, services/, middleware/, sources/)
2. CORS lockdown + API key middleware
3. Secrets to env vars + .env file
4. Extract routes from main.py -> routes/
5. Extract service logic -> services/
6. Slim down main.py to ~80 lines
7. Remove food module from Ingest
8. Add GET /emails query endpoint
9. Test: all existing endpoints still work via curl
10. Update Cointrail to pass API key + use new email endpoint for food analytics
11. Docker rebuild and verify

**Session 3: Task Queue + Data Quality**
1. Implement SQLite task queue table
2. Build task runner (background thread)
3. Convert sync operations to enqueue tasks
4. Add GET /tasks and GET /tasks/{id} endpoints
5. Fix PDF password matching (bank-aware, from env vars)
6. Fix CC date normalization in all parsers
7. Audit and improve SMS parser (add missing patterns)
8. Fix cross-matcher (date format alignment)
9. Run full re-process: PDFs -> SMS -> cross-match -> enrichment
10. Verify data quality metrics

**Session 4: Source Abstraction + Content Types**
1. Define DataSource abstract class
2. Wrap SMS, Email, PDF into source implementations
3. Add content type classification to ingestion pipeline
4. Add GET /content query endpoint
5. Update downstream apps to use content types

**Session 5: SMS Solution**
1. Research and test Tasker selective forwarding
2. Set up chosen SMS solution
3. Update latest SMS backup XML and import
4. Test end-to-end: SMS -> Ingest -> Cointrail

**Session 6+: Future sources**
- Telegram bot setup
- Notification forwarding
- Webhook system for real-time downstream notifications

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Restructure breaks Cointrail | High | Keep all endpoint URLs identical. Test each route after extraction. |
| SQLite locks under queue load | Medium | Use WAL mode (already set). Queue runs single-threaded. |
| PDF passwords still do not work | Medium | Add GET /pdf/unresolved endpoint for manual debugging. |
| SMS solution still breaks banking apps | High | Test Tasker in isolation first. Have XML backup as fallback. |
| Food module move breaks Cointrail food tab | Medium | Build email query endpoint first. Migrate food logic to Cointrail before removing from Ingest. |

---

## Current State Snapshot (2026-04-01)

| Metric | Value |
|--------|-------|
| Total SMS stored | 3,690 |
| Financial SMS | 2,633 |
| SMS parsed into transactions | 2,517 |
| Unprocessed financial SMS | 116 |
| Total emails synced | 6,770 |
| Financial emails | 2,692 |
| PDF attachments downloaded | 64 |
| Total PDFs on disk | 106 |
| Encrypted (unreadable) PDFs | 97 |
| Readable PDFs | 9 |
| Total transactions (non-duplicate) | 1,808 |
| Uncategorized transactions | 2 (0.1%) |
| Duplicate transactions | 354 |
| Unmatched card transactions | 563 |
| Active credit cards | 10 |

---

## Success Criteria

After all sessions complete:
- [ ] main.py is <100 lines (just wiring)
- [ ] All secrets in env vars, no hardcoded passwords
- [ ] CORS restricted to known origins
- [ ] API key authentication working
- [ ] Background task queue processing all async operations
- [ ] >95% of PDFs decrypted and parsed
- [ ] >95% of financial SMS parsed into transactions
- [ ] Cross-matcher working correctly (date formats fixed)
- [ ] <1% uncategorized transactions
- [ ] Food analytics moved to Cointrail
- [ ] Source abstraction in place for future extensibility
- [ ] SMS flowing reliably from phone to Ingest
