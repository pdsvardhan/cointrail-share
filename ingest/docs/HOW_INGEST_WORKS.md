# How Ingest Works

> A visual tour of the personal data ingestion service that powers Cointrail.
> Everything here is diagram-first — read the pictures, the text is just captions.

---

## 1. The 30-second version

```mermaid
flowchart LR
    subgraph Inputs["📥 Raw data sources"]
        S["📱 SMS<br/>(phone → MacroDroid)"]
        E["📧 Gmail<br/>(OAuth2)"]
        P["📄 PDF statements<br/>(email attachments)"]
    end

    I["🧠 ingest service<br/><i>FastAPI + SQLite</i>"]

    subgraph Outputs["📤 Clean, enriched data"]
        T["💰 transactions"]
        C["💳 cards"]
        R["🔁 recurring"]
        EN["✨ enrichments<br/>(restaurant, product, items)"]
    end

    S --> I
    E --> I
    P --> I
    I --> T
    I --> C
    I --> R
    I --> EN

    Cointrail["🌐 cointrail<br/>(Next.js frontend)"]
    T -.reads.-> Cointrail
    C -.reads.-> Cointrail
    R -.reads.-> Cointrail
    EN -.reads.-> Cointrail

    style I fill:#3b82f6,color:#fff
    style Cointrail fill:#10b981,color:#fff
```

**What it does:** Ingest collects raw financial data from three sources (SMS, Gmail, PDF statements), cleans it, deduplicates it, categorizes it, enriches it with merchant/product detail, and exposes it via a FastAPI REST interface that Cointrail queries.

**Where it runs:** Docker container on VAULT7A, port 8600. Single container, single SQLite DB at `/data/ingest.db`.

---

## 2. System layout

```mermaid
flowchart TB
    subgraph Phone["📱 Phone"]
        Macro["MacroDroid<br/>triggers on bank SMS"]
    end

    subgraph Internet["☁️ Internet"]
        Gmail[("📧 Gmail API<br/>OAuth2")]
    end

    subgraph Vault7a["🏠 VAULT7A server"]
        direction TB

        NPM["🔒 nginx-proxy-manager<br/>(HTTPS + basic auth)"]

        subgraph Net["projects-net Docker network"]
            Ingest["🧠 ingest<br/>:8600<br/><i>FastAPI</i>"]
            Cointrail["🌐 cointrail<br/>:8501<br/><i>Next.js</i>"]
        end

        Volume[("💾 /data/ingest.db<br/>SQLite WAL")]
    end

    User["👤 Browser"] -->|HTTPS| NPM
    NPM -->|HTTP| Cointrail
    Cointrail -->|HTTP + X-API-Key| Ingest
    Ingest --- Volume

    Macro -->|HTTPS POST /sms| NPM
    NPM -->|HTTP| Ingest
    Ingest -->|OAuth2| Gmail

    style Ingest fill:#3b82f6,color:#fff
    style Cointrail fill:#10b981,color:#fff
    style NPM fill:#f59e0b,color:#fff
```

**Key points:**
- **One SQLite file** lives on a Docker volume. WAL mode lets multiple readers work concurrently with the single writer.
- **Two Docker containers** on the same network: `ingest` and `cointrail`. They talk to each other by container name, never through the internet.
- **API key authentication** — Cointrail sends `X-API-Key` header on every call. Without it, ingest returns 401.
- **MacroDroid on the phone** POSTs new bank SMS to `/sms` so ingest can process them in near-realtime.

---

## 3. The three data sources

Every source implements the same `DataSource` abstract interface — that's how ingest stays future-proof (Telegram, push notifications, etc. can be added the same way).

```mermaid
classDiagram
    class DataSource {
        <<abstract>>
        +source_name: str
        +sync(**kwargs) dict
        +classify(raw) ContentType
        +process(raw) List[IngestedItem]
        +get_stats() dict
    }

    class SmsSource {
        +source_name = "sms"
        +sync() pull queued SMS
        +process() parse via regex
    }

    class EmailSource {
        +source_name = "email"
        +sync() Gmail API
        +process() download attachments
    }

    class PdfSource {
        +source_name = "pdf"
        +sync() scan unparsed attachments
        +process() bank-aware parser
    }

    DataSource <|-- SmsSource
    DataSource <|-- EmailSource
    DataSource <|-- PdfSource
```

Each source produces `IngestedItem` objects with a shared schema (amount, merchant, date, card_last4, bank, txn_type, confidence) that get written to the `transactions` table.

---

## 4. How a transaction travels through the pipeline

This is the journey of a single ₹450 Swiggy order from your phone to the Categories drill-down in Cointrail.

```mermaid
sequenceDiagram
    participant Phone as 📱 Phone (MacroDroid)
    participant Ingest as 🧠 /sms endpoint
    participant DB as 💾 sms table
    participant Queue as 🔁 hourly cron
    participant Parser as 🛠️ sms parser
    participant Txn as 💰 transactions table
    participant Email as 📧 email fetcher
    participant Enrich as ✨ enrichment
    participant Relink as 🔗 relink
    participant Cointrail as 🌐 Cointrail

    Phone->>Ingest: POST /sms<br/>"Rs.450 spent on HDFC Card 9434 at SWIGGY"
    Ingest->>DB: INSERT raw SMS (is_processed=0)

    Note over Queue: Top of the hour fires
    Queue->>Parser: sms_process task
    Parser->>DB: SELECT WHERE is_processed=0
    Parser->>Txn: INSERT txn<br/>amount=450, merchant=Swiggy,<br/>card_last4=9434, source=sms
    Parser->>DB: UPDATE is_processed=1

    Queue->>Email: email_sync task
    Email->>Email: fetch "Swiggy order delivered" email

    Queue->>Enrich: enrichment task
    Enrich->>Enrich: extract restaurant="T Grill"<br/>from email body
    Enrich->>Enrich: try to match<br/>by amount+date (±1 day)
    alt amount extracted from email
        Enrich->>Txn: LINK email → txn<br/>UPDATE merchant = "Swiggy - T Grill"
    else no amount in email (orphan)
        Enrich->>DB: INSERT enrichment with<br/>transaction_id=NULL
    end

    Queue->>Relink: relink_enrichments task
    Relink->>DB: find orphan enrichments
    Relink->>Txn: match by merchant pattern<br/>+ closest date
    Relink->>Txn: LINK + upgrade merchant string

    Cointrail->>Ingest: GET /analytics/merchant-detail?<br/>merchant=Swiggy
    Ingest->>Cointrail: txns + enrichments
    Note over Cointrail: "Swiggy - T Grill" shown<br/>in Food & Dining drill-down
```

---

## 5. The hourly pipeline (cron)

Every hour at :00 the ingest service enqueues 6 tasks to its background queue. They run sequentially, each depending on the previous step.

```mermaid
flowchart TD
    Start(["⏰ :00 every hour"]) --> T1

    T1["1️⃣ email_sync<br/><i>pull new Gmail, download PDF attachments</i>"]
    T2["2️⃣ pdf_process<br/><i>decrypt + parse unparsed attachments</i>"]
    T3["3️⃣ sms_process<br/><i>regex-parse pending SMS into transactions</i>"]
    T4["4️⃣ cross_match<br/><i>dedupe SMS↔PDF same-transaction</i>"]
    T5["5️⃣ enrichment<br/><i>match email receipts to transactions</i>"]
    T6["6️⃣ relink_enrichments<br/><i>link orphans by date proximity,<br/>upgrade bare merchants</i>"]

    T1 --> T2 --> T3 --> T4 --> T5 --> T6 --> End(["💾 DB is now up-to-date<br/>Cointrail sees fresh data on next query"])

    style T1 fill:#dbeafe
    style T2 fill:#dbeafe
    style T3 fill:#dbeafe
    style T4 fill:#fef3c7
    style T5 fill:#d1fae5
    style T6 fill:#d1fae5
```

**Why the order matters:** Each step needs the previous one to have finished. PDFs can only be parsed after they've been downloaded (step 1). SMS and PDF can only be cross-matched after both have been parsed (steps 2+3). Enrichment only finds transactions to link to after they exist. Relinking only helps after enrichment has created the orphan enrichments.

You can trigger the full pipeline manually with `POST /cron/trigger`.

---

## 6. Database schema (the important tables)

```mermaid
erDiagram
    sms ||--o{ transactions : "parsed into"
    emails ||--o{ email_attachments : "has"
    emails ||--o{ email_enrichments : "source of"
    email_attachments ||--o{ transactions : "PDF parsed into"
    transactions ||--o| email_enrichments : "enriched by"
    transactions }o--|| cards : "belongs to"
    transactions }o--|| statements : "part of"
    cards ||--o{ statements : "has"
    cards ||--o{ cc_payments : "has payments"

    sms {
        int id PK
        string sender
        text body
        int is_financial
        int is_processed
    }

    emails {
        int id PK
        string gmail_id UK
        string sender_email
        string subject
        text body_text
        text body_html
        int is_financial
    }

    email_attachments {
        int id PK
        int email_id FK
        string file_path
        int is_parsed
    }

    transactions {
        int id PK
        float amount
        string merchant
        string category
        string date
        string source "sms | pdf | email"
        string card_last4
        int card_id FK
        int is_duplicate
        string confidence "low | medium | high | enriched | manual"
    }

    cards {
        int id PK
        string bank
        string card_name
        string last4 UK
        int active
    }

    statements {
        int id PK
        int card_id FK
        int month
        int year
        string pdf_path
        float total_amount
    }

    email_enrichments {
        int id PK
        int email_id FK
        int transaction_id FK "NULL = orphan"
        string enrichment_type
        string brand
        text extracted_data "JSON"
    }

    merchant_aliases {
        int id PK
        string pattern UK
        string merchant_name
        string category
    }

    cc_payments {
        int id PK
        string card_last4
        float amount
        string date
    }
```

**The `transactions` table is the centre of gravity.** Everything else either feeds into it (SMS, emails, PDFs) or annotates it (enrichments, aliases, cards, statements).

---

## 7. Classification flow (which category does a transaction belong to?)

When a raw SMS or PDF line becomes a transaction, it runs through a 3-pass categorizer:

```mermaid
flowchart TD
    Raw["Raw merchant text<br/><i>e.g. 'BUNDL TECH PVT LTD BANGALORE'</i>"]

    P1["Pass 1: Alias table lookup<br/><i>exact/contains match in merchant_aliases</i>"]
    P2["Pass 2: Keyword fallback<br/><i>Food|Shopping|Bills keywords</i>"]
    P3["Pass 3: Heuristics<br/><i>P2P UPI? rent address? ATM?</i>"]

    Out1["✅ merchant: Swiggy<br/>category: Food & Dining"]
    Out2["⚠️ merchant: as-is<br/>category: keyword best-guess"]
    Out3["⚠️ merchant: 'John Doe'<br/>category: Transfer"]
    Out4["❓ category: Uncategorized<br/>low confidence"]

    Raw --> P1
    P1 -->|hit| Out1
    P1 -->|miss| P2
    P2 -->|hit| Out2
    P2 -->|miss| P3
    P3 -->|P2P UPI| Out3
    P3 -->|no rule| Out4

    style Out1 fill:#10b981,color:#fff
    style Out4 fill:#ef4444,color:#fff
```

**The alias table is seeded with 200+ merchant patterns** (`"bundl technologies"` → Swiggy, `"amzn mktplc"` → Amazon, etc.) and grows over time. When you recategorize a transaction in Cointrail, the override gets stored so it sticks.

---

## 8. Cross-matching (SMS ↔ PDF deduplication)

A single credit card swipe typically shows up **twice**: once as an instant SMS alert and once as a line on the monthly PDF statement. Cross-matcher finds these pairs and marks one as a duplicate.

```mermaid
flowchart LR
    subgraph SMS["📱 SMS txn"]
        A["Rs 450 on<br/>HDFC Card 9434<br/>at SWIGGY<br/>2026-04-06"]
    end

    subgraph PDF["📄 PDF txn"]
        B["04/06/2026<br/>SWIGGY BANGALORE<br/>₹450.00<br/>Card ending 9434"]
    end

    Match["🔍 Cross-matcher v2<br/><br/>• Same card_last4?<br/>• Amount within ±1 rupee?<br/>• Date within ±2 days?<br/>• SMS source=cc?<br/>• Merchant plausibility check"]

    Result["✅ MATCH<br/>SMS.is_duplicate=1<br/>matched_txn_id = PDF.id"]
    Skip["⏭️ SKIP<br/>UPI transfers/rent/ATM<br/>never match CC lines"]

    A --> Match
    B --> Match
    Match --> Result
    Match --> Skip

    style Result fill:#10b981,color:#fff
```

**Conservative rules:** Only SMS with `source="cc"` and non-NONCC categories can match PDF lines. Zero false duplicates in production today.

---

## 9. The enrichment system (where "Swiggy" becomes "Swiggy - T Grill")

Enrichment is a parallel layer that scans financial emails (Swiggy, Amazon, Uber, Zomato, BookMyShow, etc.) and extracts structured details (restaurant names, product names, item lists) that get attached to matching transactions.

```mermaid
flowchart TB
    subgraph Scan["1. Scan emails"]
        E1["Swiggy delivery email<br/><i>body: items + total</i>"]
        E2["Amazon shipped email<br/><i>subject: product name<br/>body_html: full HTML</i>"]
        E3["Uber receipt<br/><i>body: fare + route</i>"]
    end

    subgraph Parse["2. Brand-specific parsers"]
        P1["extract_restaurant_from_swiggy()<br/>parse_swiggy_items()"]
        P2["parse_amazon_html_body()<br/>parse_amazon_subject()"]
        P3["parse_generic_subject()"]
    end

    subgraph Match["3. Try to match a transaction"]
        M["Amount ± 5% + date ± 1 day"]
    end

    subgraph Store["4. Write email_enrichments row"]
        S1["✅ transaction_id = 1234<br/>extracted_data = JSON"]
        S2["⚠️ transaction_id = NULL<br/>(orphan — no amount/match)"]
    end

    subgraph Relink["5. relink step"]
        R["Find bare txns for same brand<br/>within time window<br/>(Amazon: -14d/+2d,<br/>Swiggy: ±3d)"]
    end

    E1 --> P1
    E2 --> P2
    E3 --> P3
    P1 --> Match
    P2 --> Match
    P3 --> Match
    Match -->|matched| S1
    Match -->|no match| S2
    S2 --> R
    R -->|linked| S1

    Up["6. upgrade_bare_merchants<br/>Swiggy → 'Swiggy - T Grill'"]
    S1 --> Up

    style S1 fill:#10b981,color:#fff
    style S2 fill:#fef3c7
```

**Why the relink step exists:** Many email receipts don't contain the transaction amount (especially "delivered" notifications), so the original amount-matcher can't link them. The relink step matches by **brand + date proximity** instead, with asymmetric windows per brand (Amazon "delivered" emails arrive up to 14 days after the charge, so it searches mostly backwards).

**Current coverage (April 2026):**
| Category | Before relink | After relink |
|---|---|---|
| Food & Dining | 25% | **36%** |
| Shopping (Amazon) | 1% | **25%** |

---

## 10. Complete route map

All endpoints the ingest service exposes (every non-public route requires `X-API-Key`):

| Prefix | Route | Purpose |
|---|---|---|
| `/health` | GET | Liveness probe (public, no auth) |
| `/sms` | POST, /parse, /process-pending, /import/xml | Receive & parse SMS |
| `/email` | /sync, /status, /auth, /list | Gmail sync + query |
| `/pdf` | /upload, /parse-path, /process-all, /process-unparsed, /status | PDF statement pipeline |
| `/cards` | /, /summary, /{id}/statements, /{id}/transactions, /monthly-summary, /statements/backfill, /payments/backfill | Credit card queries |
| `/transactions` | GET, PUT {id}, /recategorize-all, /recurring/detect | Transaction CRUD |
| `/analytics` | /overview, /categories, /trends, /heatmap, /day-transactions, /merchant-detail | Dashboard queries |
| `/content` | / | Unified content query (future) |
| `/tasks` | / | Background queue status |
| `/cron` | /trigger, /status | Manual cron trigger |
| `/system` | /health, /stats, /cross-match, /aliases, /categories, /analysis/*, /enrichments, /upi/* | Admin operations |

---

## 11. Why it's built this way

| Design choice | Reason |
|---|---|
| **SQLite, not Postgres** | Single-user service, ~10K transactions. WAL mode handles concurrent readers. Zero operational cost. |
| **FastAPI, not Django/Flask** | Async-friendly, automatic OpenAPI docs at `/docs`, minimal boilerplate. |
| **In-process task queue (no Celery/Redis)** | Single container, single user — an external broker would be overkill. SQLite-backed queue is good enough and survives restarts. |
| **`DataSource` abstract base** | Adding a new source (Telegram bot, push notifications) means implementing one class, not rewriting the pipeline. |
| **API key auth, not OAuth** | Cointrail is the only client. One static secret in `.env` is simpler and auditable. |
| **Volume-mounted DB** | Lets you `docker compose up --build` safely without losing data. Also lets scripts hot-read the DB for debugging. |
| **Hourly cron instead of event-driven** | Debounces API rate limits (Gmail, MacroDroid) and keeps the system calm. Financial data rarely needs sub-hour latency. |

---

## 12. Quick glossary

| Term | Meaning |
|---|---|
| **Source** | Where raw data comes from: `sms`, `email`, or `pdf`. |
| **Transaction** | A single financial event (debit or credit). |
| **Cross-match** | Deduplication between SMS alerts and PDF statement lines for the same physical swipe. |
| **Enrichment** | Attaching structured metadata (restaurant, product, items) to a transaction from a matching receipt email. |
| **Relink** | Second-pass matcher that links orphan enrichments to bare transactions by brand + date proximity. |
| **Confidence** | How sure ingest is about a transaction's category: `low` → `medium` → `high` → `enriched` → `manual` (user override). |
| **Alias** | A merchant name pattern that maps a raw bank description to a clean merchant + category. |
| **Orphan enrichment** | An email enrichment that exists but isn't linked to any transaction (usually because the email didn't include an amount). |

---

*Last updated: 2026-04-09 (iteration 26)*
