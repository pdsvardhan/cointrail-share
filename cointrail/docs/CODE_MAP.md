# Cointrail — Code Map (Stage 3.1 catchup snapshot)

> Locked: 2026-05-16. A walking tour of where things live.

## Top-level layout

```
cointrail/
├── app/                     # Next.js 15 App Router
├── components/              # React components (presentation + behavior)
├── lib/                     # Utilities (db, ingest client, formatters, logos, ...)
├── config/                  # App-level config
├── services/                # Legacy Python services (kept for reference)
├── data/                    # Local SQLite + SMS imports + statement PDFs
├── android/                 # Capacitor Android wrap
├── mobile/webroot/          # Static fallback assets for the Capacitor WebView
├── public/                  # Static public assets
├── architecture/            # Mermaid diagrams + rendered SVG
├── decisions/               # ADRs (markdown)
├── docs/                    # Long-form docs
├── staging/                 # ND1 archive (write-once)
├── __tests__/               # Vitest tests (3 today)
├── docker-compose.yml       # Container definition (cointrail service)
├── Dockerfile               # Multi-stage standalone next build
├── capacitor.config.ts      # Capacitor app id + appName + WebView URL
├── package.json             # name=cointrail; next + tailwind + capacitor + recharts
├── tsconfig.json
├── next.config.ts
├── postcss.config.mjs
├── eslint.config.mjs
├── vitest.config.ts
└── README.md
```

## app/ — Next.js App Router

```
app/
├── layout.tsx               # Root layout: Outfit + JetBrainsMono fonts, ClientLayout wrap
├── page.tsx                 # Homepage with 4 section tiles
├── globals.css              # glass-liquid v1 design tokens (authoritative)
├── expenses/
│   ├── page.tsx             # 4-tab dashboard
│   └── recurring/
│       └── page.tsx
├── cards/
│   ├── page.tsx             # Portfolio
│   └── [cardId]/
│       └── page.tsx         # Card detail
├── calculators/             # DROPPED in SO1 (2026-05-15); code still on disk
│   ├── page.tsx
│   ├── emi/page.tsx, expense-fire/page.tsx, fire/page.tsx,
│   ├── rent-vs-buy/page.tsx, sip/page.tsx, tax/page.tsx
└── api/                     # iter-18 CH4 removed analysis/, cards/monthly-summary/, sms-*/
    ├── cards/
    │   ├── route.ts
    │   ├── [id]/transactions/route.ts
    │   ├── statements/route.ts
    │   └── sync-gmail/route.ts
    ├── expenses/
    │   ├── route.ts                      # CRUD list/create
    │   ├── [id]/route.ts                 # update/delete
    │   └── dashboard/
    │       ├── route.ts                  # overview totals
    │       ├── categories/route.ts
    │       ├── trends/route.ts
    │       ├── heatmap/route.ts
    │       ├── day-transactions/route.ts # day-drill
    │       └── merchant-detail/route.ts
    ├── food/
    │   ├── route.ts
    │   └── orders/route.ts
    ├── recurring/
    │   ├── route.ts
    │   ├── detect/route.ts
    │   ├── generate/route.ts
    │   └── [id]/route.ts
```

## components/

```
components/
├── analysis/                # Aggregate spend analysis pieces
├── cards/                   # 3D CreditCardComponent + portfolio + detail page parts
├── calculators/             # DROPPED; on-disk until cleanup
├── expenses/                # 4-tab dashboard parts (Overview, Categories, Trends, Recurring, DayTransactionsPanel)
├── layout/                  # ClientLayout (top nav + theme toggle)
└── shared/                  # MerchantLogo, EmptyState, LoadingShimmer, ErrorBoundary, StatCard primitives
```

## lib/

| File                | Purpose                                                                   |
| ------------------- | ------------------------------------------------------------------------- |
| `db.ts`             | better-sqlite3 connection w/ WAL + foreign_keys + busy_timeout            |
| `migrate.ts`        | `ensureMigrated()` — idempotent CREATE TABLE; `normalizeDates()` backfill |
| `ingest.ts`         | `ingestFetch()` — single Ingest HTTP client with X-API-Key                |
| `formatters.ts`     | `formatINR()`, `formatPercent()`, Indian number grouping                  |
| `categories.ts`     | Seed categories list + helpers                                            |
| `constants.ts`      | App-wide constants                                                        |
| `food-helpers.ts`   | Food order parsing/grouping (consumed by /api/food/orders)                |
| `logos.ts`          | Clearbit + Google Favicons fallback for merchant logos                    |
| `normalize-date.ts` | ISO date enforcement (backfilled by migrator on boot)                     |

## services/

Python services kept for reference (legacy from pre-Ingest era):

- `services/pdf-parser/` — `main.py`, `auth_gmail.py`, `categorizer.py` (now living in Ingest)

These are NOT run by Cointrail. Safe to delete in a future cleanup iteration.

## android/

Capacitor Android Studio project:

- `app/build.gradle` — applicationId `com.example.cointrail`, namespace same, compileSdk 36, versionCode/versionName from env
- `app/src/main/AndroidManifest.xml` — declares SMS_RECEIVED permission + receiver
- `app/src/main/java/com/example/cointrail/`
  - `MainActivity.java` — extends `BridgeActivity`, registers `SmsReceiverPlugin`
  - `SmsBroadcastReceiver.kt` — listens for `android.provider.Telephony.SMS_RECEIVED`, builds JSON, POSTs to Ingest
  - `SmsReceiverPlugin.kt` — Capacitor plugin glue
- `app/src/main/res/values/strings.xml` — app_name=Cointrail
- `app/src/main/assets/capacitor.config.json` — sync output of root capacitor.config.ts
- `app/src/main/assets/public/` — offline.html + cached index.html

## data/

| Path                | Purpose                                                                          |
| ------------------- | -------------------------------------------------------------------------------- |
| `data/finance.db`   | Local SQLite — manual entries, seed categories, local card metadata, settings KV |
| `data/sms_imports/` | Historic SMS XML staging (legacy import path)                                    |
| `data/statements/`  | Downloaded CC statement PDFs                                                     |


## architecture/

```
architecture/
├── system-context.mmd  + .svg   # Cloudflare → Cointrail → Ingest topology
├── data-flow.mmd       + .svg   # SMS/Email/PDF → Ingest cron → Cointrail UI flows
└── auth-flow.mmd       + .svg   # CF Tunnel + LAN + Android X-API-Key
```

Rendered with `mmdc -p ~/.puppeteer-config.json` (no-sandbox required for headless Chrome in this container).

## decisions/

- `2026-05-15-initial-staging.md` — ADR-001 (auto-created on Stage 1 lock)
- `2026-05-15-ui-master-plan-locked.md` — ADR-002 (Stage 2.2 lock)
- `2026-05-16-stage-2-and-3-catchup-complete.md` — ADR-003 (Stage 2 + 3 catchup completion)

## staging/

- `archive/ND1-2026-05-15-214335.yaml` — archived after Stage 1 internal handoff

## **tests**/

| File                   | Targets             | Status  |
| ---------------------- | ------------------- | ------- |
| `formatters.test.ts`   | lib/formatters.ts   | passing |
| `food-helpers.test.ts` | lib/food-helpers.ts | passing |
| `logos.test.ts`        | lib/logos.ts        | passing |

Coverage gap: API routes, components, db.ts, migrate.ts, ingest.ts, normalize-date.ts. See `docs/TEST_PLAN.md`.

## Naming + convention reminders

See `docs/CODING_GUIDELINES.md`. Highlights:

- All transactional data via `lib/ingest.ts` only
- Local SQLite holds only manual entries + categories + settings + local card metadata
- Currency values: `formatINR()` mandatory + `tabular-nums` Tailwind class
- Theme tokens: read CSS vars from `globals.css` only; never hardcode
- New components follow glass-liquid v1 contract
