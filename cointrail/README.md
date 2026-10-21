# Cointrail -- Personal Finance Tracker

Track expenses, manage credit cards, analyze spending patterns, and plan your financial future.

<!-- Screenshot placeholder: Add a screenshot of the dashboard here -->

## Features

- **Expense Dashboard** -- 4-tab layout: Overview, Categories, Trends, and Heatmap with day-level drill-down
- **Credit Card Management** -- 3D animated cards, billing cycle tracking, statement history, and payment tracking
- **Recurring Expense Detection** -- Automatic detection of subscriptions, EMIs, and yearly charges with next-expected-date forecasting
- **Food Analytics** -- Track and analyze food delivery orders and spending
- **Android APK (capture-at-source)** -- A Capacitor wrapper with a native Kotlin SMS `BroadcastReceiver` POSTs bank SMS to the Ingest service; auto-versioned and distributed via Obtainium from builds.example.com
- **Dark/Light Theme** -- Glassmorphism UI with smooth theme transitions

> UPI/merchant classification and SMS/email/PDF parsing live in the **Ingest** service, not in this app. Cointrail only displays the results.

## Tech Stack

| Layer     | Technology                                   |
| --------- | -------------------------------------------- |
| Framework | Next.js 15 (App Router)                      |
| Styling   | Tailwind CSS 4                               |
| Database  | better-sqlite3 (local data + manual entries) |
| Charts    | Recharts                                     |
| Animation | motion/react (Framer Motion)                 |
| UI        | Radix UI primitives, Lucide icons, Vaul      |
| Deploy    | Docker on private network                    |

## Architecture

Cointrail is a **frontend + manual expense entry** application. It does not parse SMS or emails directly. Instead, it queries the **Ingest** service for all transaction data, card information, and analytics over a shared Docker network (`projects-net`).

```
User --> Cointrail (Next.js, port 8501)
              |
              +--> Local SQLite (manual entries, food orders)
              |
              +--> Ingest Service (http://ingest:8600)
                    (transactions, cards, analytics, SMS, PDF, email)
```

Both services run on the same Docker network (see the root `docker-compose.yml`). Cointrail proxies API calls to Ingest from its Next.js API routes.

## Prerequisites

- Node.js 20+
- Docker and Docker Compose
- **Ingest service** reachable at `INGEST_URL` (the root `docker-compose.yml` runs both)

## Setup

1. Clone the repository:

   ```bash
   git clone <repo-url> cointrail
   cd cointrail
   ```

2. Create a `.env` file:

   ```env
   INGEST_API_KEY=your-api-key-here
   INGEST_URL=http://ingest:8600
   ```

3. Build and run:

   ```bash
   docker compose up -d --build
   ```

4. Access the app at `http://<server-ip>:8501`

## Environment Variables

| Variable         | Description                            | Default              |
| ---------------- | -------------------------------------- | -------------------- |
| `INGEST_API_KEY` | API key for authenticating with Ingest | _(required)_         |
| `INGEST_URL`     | URL of the Ingest service              | `http://ingest:8600` |

## Project Structure

```
cointrail/
  app/
    api/                  # Next.js API routes (proxy to Ingest + local)
      cards/              # Card list, details, statements, sync
      expenses/           # CRUD, dashboard (overview, categories, trends, heatmap)
      food/               # Food order tracking
      recurring/          # Recurring expense detection and management
    cards/                # Card management UI (default landing; / redirects here)
    expenses/             # Expense views (dashboard + recurring)
  components/
    cards/                # Card display components (3D cards, billing)
    expenses/             # Expense table, filters, charts
    analysis/             # Analysis and chart components
    ui/                   # Shared UI primitives (Button, Dialog, Tabs, etc.)
  config/                 # App configuration
  lib/                    # Utilities, database helpers (ingest.ts, migrate.ts, formatters.ts)
  android/, mobile/       # Capacitor Android wrapper + native SMS receiver
  data/                   # SQLite database (manual entries + food)
  public/                 # Static assets
```

## API Routes

All routes are under `/api/`. Most proxy to the Ingest service; some manage local data.

### Cards (`/api/cards/*`)

| Method | Path                           | Description                       |
| ------ | ------------------------------ | --------------------------------- |
| GET    | `/api/cards`                   | List all credit cards             |
| GET    | `/api/cards/[id]/transactions` | Transactions for a specific card  |
| GET    | `/api/cards/statements`        | List card statements              |
| POST   | `/api/cards/sync-gmail`        | Trigger Gmail sync for statements |

### Expenses (`/api/expenses/*`)

| Method | Path                                       | Description                        |
| ------ | ------------------------------------------ | ---------------------------------- |
| GET    | `/api/expenses`                            | List/search expenses               |
| POST   | `/api/expenses`                            | Create a manual expense            |
| PUT    | `/api/expenses/[id]`                       | Update an expense                  |
| GET    | `/api/expenses/dashboard`                  | Dashboard overview (totals, stats) |
| GET    | `/api/expenses/dashboard/categories`       | Category breakdown                 |
| GET    | `/api/expenses/dashboard/trends`           | Monthly spending trends            |
| GET    | `/api/expenses/dashboard/heatmap`          | Daily spending heatmap             |
| GET    | `/api/expenses/dashboard/day-transactions` | Transactions for a specific day    |

### Recurring (`/api/recurring/*`)

| Method | Path                      | Description                        |
| ------ | ------------------------- | ---------------------------------- |
| GET    | `/api/recurring`          | List recurring expenses            |
| GET    | `/api/recurring/detect`   | Detect recurring patterns          |
| POST   | `/api/recurring/generate` | Generate recurring expense entries |
| PUT    | `/api/recurring/[id]`     | Update a recurring expense         |

### Food (`/api/food/*`)

| Method | Path               | Description               |
| ------ | ------------------ | ------------------------- |
| GET    | `/api/food`        | Food spending analytics   |
| GET    | `/api/food/orders` | List food delivery orders |

The SMS proxies (`/api/sms-ingest`, `/api/sms-parse`, `/api/sms-sync`), `/api/analysis` and
`/api/cards/monthly-summary` were removed in iter-18 (CH4): nothing in the UI called them, and the
SMS proxies let any LAN client inject SMS into Ingest with the server-side key. The phone forwarder
posts to Ingest `POST /sms` directly.

## Note

Cointrail is a **single-user** app with **no authentication layer** -- access control is delegated to the network. It is reached privately on the LAN at `http://<server-ip>:8501` and published at `https://cointrail.example.com` via a **Cloudflare Tunnel** (the tunnel, not app auth, is the access boundary). Treat the public URL accordingly; there is no login.
