# AGENTS.md — orientation for AI coding agents

Read this first, then `README.md`. Two services in one repo: `ingest/` (Python, source of truth for money data) and
`cointrail/` (Next.js UI). **All parsing, merging, categorising and analytics live in Ingest; Cointrail only displays
and proxies.** If a number looks wrong in the UI, the bug is almost always in Ingest.

## Data flow
```
SMS (POST /sms)  ──┐
Gmail alert mails ─┼─▶ modules/txn_store.insert_transaction ─▶ modules/merge (link copies of one purchase)
Statement PDFs   ──┘        │ categorizer (alias rules, payee rules)        │
                            ▼                                               ▼
                    transactions table  ◀── pairing (own-account moves) ── cc_payments / statements (bank-stated totals)
                            │
        routes/* + services/* (analytics, cards, recurring, emi_plans)  ──REST + X-API-Key──▶ cointrail/app/api/* ──▶ React components
```
An hourly APScheduler cron (`main.py`, `services/queue.py`) runs: email sync → PDF process → SMS process → merge/cross-match →
enrichment/relink → health checks. Every source writes through `txn_store.insert_transaction`; never INSERT into
`transactions` directly.

## Ingest map (`ingest/`)
| Path | What |
|---|---|
| `main.py`, `config.py`, `db.py` | App + cron wiring; env config; schema and ordered migrations (`init_db`, `run_*_migrations`, cards config sync) |
| `middleware/security.py` | CORS + API-key auth. **Fails closed**: no `INGEST_API_KEY` ⇒ 503 on everything except `/health`, `/docs` (dev override `INGEST_ALLOW_NO_AUTH=1`) |
| `routes/` | HTTP layer only: `sms`, `email`, `pdf`, `transactions`, `cards`, `analytics`, `food`, `system`, `tasks`, `content` |
| `services/` | `cards_service` (billing cycles/due dates), `recurring_service`, `emi_plans`, `queue` (SQLite task queue) |
| `modules/sms/` | `parser.py` (per-bank regex), `processing.py` (`process_sms_row` classifies: transaction / noise / bill_payment / statement / non_financial / quarantined), `receiver.py` |
| `modules/email/` | Gmail client, bank-alert e-mails as a transaction source, order-email enrichment (`relink`) |
| `modules/pdf/`, `sources/pdf/` | Statement parsers per bank; password handling via `PDF_PASSWORD_*` |
| `modules/merge.py`, `pairing.py`, `cross_matcher.py` | De-duplication (identity-based linking, ADR-006) and own-account pairing (ADR-011) |
| `modules/categorizer.py` | Merchant alias table + payee rules + `kind` derivation. Personal payees go in the "Personal payee rules" section |
| `modules/quarantine.py`, `digest.py`, `notify.py` | Fail-loud: unparseable money messages are quarantined, daily digest, Telegram/ntfy/webhook alerts |
| `scripts/` | `seed_demo.py` (synthetic data), `check_data_integrity.py`, `build_sms_corpus.py` (author's private tooling), `iter*_steps.py` (one-off deploy steps; some unit tests import them) |
| `tests/unit/`, `tests/corpus/` | pytest. `conftest.py` builds a throw-away SQLite per test; the golden SMS corpus is `tests/corpus/sms_corpus.jsonl` |
| `decisions/adr-*.md` | **Read these before changing merge/categorise/parse behaviour** |

## Cointrail map (`cointrail/`)
| Path | What |
|---|---|
| `app/` | Next.js App Router. `app/cards`, `app/expenses` pages; `app/api/**` route handlers (proxy to Ingest through `lib/ingest.ts`, plus local SQLite CRUD) |
| `lib/` | Pure logic with unit tests: date ranges (`expense-range`, `dates`), card status/periods, category editing, EMI, formatters, `db.ts` (better-sqlite3 manual entries) |
| `components/` | `cards/`, `expenses/`, `layout/`, `shared/` |
| `__tests__/` | vitest; `e2e/` Playwright (needs a running instance) |
| `android/`, `mobile/`, `capacitor.config.ts` | Optional Capacitor wrapper; native Kotlin SMS `BroadcastReceiver` POSTs to Ingest `/sms` |
| `docs/` | `CODE_MAP.md`, `HOW_COINTRAIL_PULLS_DATA.md`, `FEATURES.md` (plain-English feature tour), `design-tokens.md` |

## Environment variables
Ingest: `INGEST_API_KEY` (required), `INGEST_DB`, `CARDS_CONFIG_PATH`, `STATEMENTS_DIR`, `GMAIL_CREDS_PATH`, `GMAIL_TOKEN_PATH`,
`PDF_PASSWORD_{HDFC,ICICI,HSBC_CC,HSBC_ACCOUNT,SBI,AXIS}`, `ALLOWED_ORIGINS`, `SMS_BACKUP_SENDERS`, alerts
(`TELEGRAM_BOT_TOKEN[_FILE]`, `TELEGRAM_CHAT_ID[_FILE]`, `NTFY_URL`, `INGEST_ALERT_WEBHOOK`), `HOST`, `PORT`.
Cointrail: `INGEST_URL` (default `http://ingest:8600`), `INGEST_API_KEY`, `DB_PATH` (default `/data/finance.db` in Docker).

## Run / verify
```bash
cd ingest && pip install -r requirements.txt -r requirements-dev.txt && python -m pytest -q   # must stay green
cd cointrail && npm ci && npm test && npx tsc --noEmit && npm run lint
docker compose up -d --build && docker compose exec ingest python scripts/seed_demo.py       # full stack with demo data
```

## Invariants — do not break
1. **One purchase, one row.** Duplicate copies (SMS + e-mail + PDF) are *linked*, not deleted; totals exclude `is_duplicate` rows (ADR-006).
2. **Units are explicit.** Every row has `currency`, IST-calendar `date`, and a `kind` (purchase / refund / reversal / cashback / fee / interest / EMI principal / bill payment …). Spend totals depend on `kind` (ADR-007).
3. **Fail loud.** A money-looking message that can't be parsed is quarantined and surfaced in `/health`, never silently dropped (ADR-008).
4. **Bank-printed numbers win** over computed ones for due date / amount due / minimum due (ADR-009).
5. **Own-account moves are not spending** (card bill payments, self transfers, bounced debits) (ADR-011).
6. Money in tests is synthetic; keep it that way. Never commit real SMS, statements, tokens, `.env`, or `data/`.
7. DB changes are additive migrations in `db.py`, applied in the order `main.startup()` calls them (mirrored in `scripts/seed_demo.py` and `tests/unit/conftest.py`).

## Good first reads
`ingest/docs/HOW_INGEST_WORKS.md` → `ingest/decisions/adr-006…011` → `ingest/modules/txn_store.py` → `ingest/modules/sms/processing.py`
→ `cointrail/docs/HOW_COINTRAIL_PULLS_DATA.md` → `cointrail/lib/ingest.ts`.

## Not included / expect gaps
The author's private SMS corpus, real cards list, payee rules, deployment scripts, CI config and Android signing are deliberately absent.
Docs may mention "iter-NN" labels and a Cloudflare-tunnel home-server setup from the original deployment; treat them as history.
