# Ingest -- Personal Finance Data Hub

Central data ingestion and processing service for personal finance data from SMS, email (Gmail), and credit card statement PDFs.

## Features

- **SMS Parsing** -- ~45 Indian bank SMS parser patterns (coverage not yet measured)
- **Gmail Integration** -- OAuth-based email sync with automatic CC statement PDF download
- **PDF Parsing** -- Statement parsers for HDFC, HSBC, SBI, ICICI, and Axis banks
- **Cross-Source Deduplication** -- Matches transactions across SMS, email, and PDF sources
- **Merchant Categorization** -- 300+ merchant aliases mapped to spending categories
- **Recurring Expense Detection** -- Identifies subscriptions, EMIs, and yearly charges with frequency analysis
- **UPI Classification** -- Classify and map unknown UPI merchant IDs
- **Background Task Queue** -- APScheduler-based hourly cron for automated pipeline execution
- **RESTful API** -- Full data access layer for the cointrail frontend and other consumers

## Tech Stack

| Layer      | Technology                      |
|------------|---------------------------------|
| Framework  | FastAPI                         |
| Language   | Python 3.11+                    |
| Database   | SQLite (WAL mode)               |
| Scheduler  | APScheduler                     |
| PDF        | pikepdf                         |
| Email      | Google Gmail API (OAuth 2.0)    |
| Deploy     | Docker on private network       |

## Architecture

```
Ingest Service
  main.py                 # FastAPI app, router registration, cron setup
  config.py               # Environment-based configuration
  db.py                   # SQLite connection, migrations
  middleware/
    security.py           # CORS setup, API key auth
  routes/
    sms.py                # SMS receive, parse, process, import
    email.py              # Gmail sync, auth status, list
    pdf.py                # PDF upload, parse, batch process
    transactions.py       # Query, update, recategorize, recurring detect
    cards.py              # Card list, summary, statements, payments, billing
    analytics.py          # Overview, categories, trends, heatmap
    system.py             # Health, stats, aliases, categories, UPI, enrichment
    tasks.py              # Background task queue
    content.py            # Content sources, types, query, summary
  modules/
    sms_parser.py         # Bank-specific SMS regex patterns
    pdf_parser.py         # PDF statement extraction
    categorizer.py        # Merchant-to-category mapping, alias seeding
    cross_matcher.py      # Cross-source deduplication
    enrichment.py         # Transaction enrichment (logos, metadata)
    recurring.py          # Recurring expense detection
  services/
    gmail.py              # Gmail API wrapper
    queue.py              # Background task queue runner
```

## Data Pipeline

The hourly cron executes the following pipeline:

```
email_sync --> pdf_process --> sms_process --> cross_match --> enrichment
```

1. **Email Sync** -- Fetches new emails from Gmail, downloads CC statement PDFs
2. **PDF Process** -- Parses downloaded statement PDFs into transactions
3. **SMS Process** -- Processes any pending/new SMS messages
4. **Cross Match** -- Deduplicates transactions across all sources
5. **Enrichment** -- Adds merchant logos, categorization, and metadata

## Prerequisites

- Python 3.11+
- Docker and Docker Compose
- Gmail OAuth credentials (for email sync)

## Setup

1. Clone the repository:
   ```bash
   git clone <repo-url> ingest
   cd ingest
   ```

2. Create a `.env` file:
   ```env
   INGEST_API_KEY=your-api-key-here
   DATA_DIR=/data
   GMAIL_CREDS_PATH=/data/gmail_tokens/credentials.json
   GMAIL_TOKEN_PATH=/data/gmail_tokens/token.json
   PDF_PASSWORD_HDFC=password1
   PDF_PASSWORD_HSBC_CC=password2
   PDF_PASSWORD_HSBC_ACCOUNT=password3
   PDF_PASSWORD_SBI=password4
   PDF_PASSWORD_ICICI=password5
   PDF_PASSWORD_AXIS=password6
   ```

3. Place Gmail OAuth `credentials.json` in the configured path.

4. Build and run:
   ```bash
   docker compose up -d --build
   ```

5. Service will be available at `http://<server-ip>:8600`

## Environment Variables

| Variable                  | Description                                  | Default                               |
|---------------------------|----------------------------------------------|---------------------------------------|
| `INGEST_API_KEY`          | API key for request authentication (fails closed if unset) | *(required)*            |
| `INGEST_DB`               | Path to SQLite database                      | `/data/ingest.db`                     |
| `STATEMENTS_DIR`          | Directory for downloaded PDF statements      | `/data/statements`                    |
| `SMS_IMPORTS_DIR`         | Directory for SMS import files               | `/data/sms_imports`                   |
| `GMAIL_CREDS_PATH`        | Path to Gmail OAuth credentials.json        | `/data/gmail_tokens/credentials.json` |
| `GMAIL_TOKEN_PATH`        | Path to Gmail OAuth token.json              | `/data/gmail_tokens/token.json`       |
| `PDF_PASSWORD_HDFC`       | Password(s) for HDFC statements (comma-sep) | *(empty)*                             |
| `PDF_PASSWORD_HSBC_CC`    | Password for HSBC CC statements             | *(empty)*                             |
| `PDF_PASSWORD_HSBC_ACCOUNT`| Password for HSBC account statements       | *(empty)*                             |
| `PDF_PASSWORD_SBI`        | Password for SBI statements                 | *(empty)*                             |
| `PDF_PASSWORD_ICICI`      | Password for ICICI statements               | *(empty)*                             |
| `PDF_PASSWORD_AXIS`       | Password for Axis statements                | *(empty)*                             |
| `HOST`                    | Server bind address                          | `0.0.0.0`                             |
| `PORT`                    | Server port                                  | `8600`                                |

## API Reference

All endpoints accept and return JSON. When `INGEST_API_KEY` is set, include `X-API-Key` header.

### Cards (`/cards`)

| Method | Path                          | Description                              |
|--------|-------------------------------|------------------------------------------|
| GET    | `/cards`                      | List all detected credit/debit cards     |
| GET    | `/cards/summary`              | Per-card spending summary                |
| GET    | `/cards/monthly-summary`      | Month-wise card spending breakdown       |
| GET    | `/cards/{card_id}/statements` | List statements for a card               |
| GET    | `/cards/{card_id}/transactions`| Transactions for a card (filterable)    |
| POST   | `/cards/statements/backfill`  | Backfill missing statement records       |
| POST   | `/cards/statements/from-sms`  | Generate statements from SMS data        |
| POST   | `/cards/payments/backfill`    | Backfill payment records                 |
| POST   | `/cards/payments/dedup`       | Deduplicate payment entries              |

### Transactions

| Method | Path                     | Description                                 |
|--------|--------------------------|---------------------------------------------|
| GET    | `/transactions`          | Query transactions (filter by card, date, category, source) |
| PUT    | `/transactions/{txn_id}` | Update transaction category or metadata     |
| POST   | `/recategorize-all`      | Re-run categorization on all transactions   |

### Recurring

| Method | Path                | Description                                      |
|--------|---------------------|--------------------------------------------------|
| GET    | `/recurring/detect` | Detect recurring expenses (subscriptions, EMIs)  |

### Analytics (`/analytics`)

| Method | Path                           | Description                          |
|--------|--------------------------------|--------------------------------------|
| GET    | `/analytics/overview`          | Spending overview (totals, averages) |
| GET    | `/analytics/categories`        | Category-wise breakdown              |
| GET    | `/analytics/trends`            | Monthly spending trends              |
| GET    | `/analytics/heatmap`           | Daily spending heatmap data          |
| GET    | `/analytics/day-transactions`  | All transactions for a given date    |

### Email (`/email`)

| Method | Path            | Description                               |
|--------|-----------------|-------------------------------------------|
| POST   | `/email/sync`   | Sync emails from Gmail (downloads PDFs)   |
| GET    | `/email/status` | Email sync status and stats               |
| GET    | `/email/auth`   | Gmail OAuth authentication status         |
| GET    | `/email/list`   | List synced emails                        |

### PDF (`/pdf`)

| Method | Path                  | Description                              |
|--------|-----------------------|------------------------------------------|
| POST   | `/pdf/upload`         | Upload a PDF statement for parsing       |
| POST   | `/pdf/parse-path`     | Parse a PDF from a server file path      |
| POST   | `/pdf/process-all`    | Process all PDF statements               |
| POST   | `/pdf/process-unparsed`| Process only unparsed PDF statements    |
| GET    | `/pdf/status`         | PDF processing status and stats          |

### SMS (`/sms`)

| Method | Path                  | Description                              |
|--------|-----------------------|------------------------------------------|
| POST   | `/sms`                | Receive and store an SMS message         |
| POST   | `/sms/parse`          | Parse a single SMS manually              |
| POST   | `/sms/process-pending`| Process all pending (unparsed) SMS       |
| POST   | `/sms/import/xml`     | Import SMS from XML backup file          |

### Tasks (`/tasks`)

| Method | Path                | Description                        |
|--------|---------------------|------------------------------------|
| GET    | `/tasks`            | List recent background tasks       |
| GET    | `/tasks/{task_id}`  | Get status of a specific task      |
| POST   | `/tasks/enqueue`    | Enqueue a new background task      |

### Content (`/content`)

| Method | Path               | Description                          |
|--------|--------------------|--------------------------------------|
| GET    | `/content/sources` | List data sources                    |
| GET    | `/content/types`   | List content types                   |
| GET    | `/content/query`   | Query content with filters           |
| GET    | `/content/summary` | Content summary and statistics       |

### System

| Method | Path                    | Description                            |
|--------|-------------------------|----------------------------------------|
| GET    | `/health`               | Service health check                   |
| GET    | `/stats`                | Overall database statistics            |
| POST   | `/cross-match`          | Run cross-source deduplication         |
| GET    | `/aliases`              | List merchant aliases                  |
| POST   | `/aliases`              | Create a new merchant alias            |
| GET    | `/categories`           | List spending categories               |
| GET    | `/categories/summary`   | Category summary with totals           |
| GET    | `/analysis/monthly`     | Monthly spending analysis              |
| GET    | `/analysis/merchants`   | Top merchants by spend                 |
| GET    | `/enrichments`          | List enrichment records                |
| POST   | `/enrichment/run`       | Trigger enrichment pipeline            |

### UPI

| Method | Path                 | Description                           |
|--------|----------------------|---------------------------------------|
| GET    | `/upi/unclassified`  | List unclassified UPI transactions    |
| POST   | `/upi/classify`      | Classify a UPI merchant               |
| GET    | `/upi/mappings`      | List UPI merchant mappings            |

## SMS Bank Coverage

Supported banks and payment providers for SMS parsing:

- HDFC Bank (credit card, debit card, account)
- HSBC (credit card, account)
- SBI (credit card, account)
- ICICI Bank (credit card, account)
- Axis Bank (credit card)
- PhonePe
- Google Pay (GPay)
- Pluxee (meal card)

## PDF Parser Coverage

Supported banks for credit card statement PDF parsing:

- HDFC Bank
- HSBC
- SBI Card
- ICICI Bank
- Axis Bank

## Note

Ingest is designed for **single-user deployment** on a private network. API key authentication is optional but recommended. Do not expose to the public internet without additional security measures.
