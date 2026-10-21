# Cointrail + Ingest

A self-hosted personal-finance stack for Indian bank/credit-card users. Two services:

| Service | Stack | Port | Job |
|---|---|---|---|
| [`ingest/`](ingest/) | Python 3.12, FastAPI, SQLite (WAL), APScheduler | 8600 | Collects bank SMS, Gmail alerts/orders and credit-card statement PDFs; parses, de-duplicates, categorises; serves everything over a REST API |
| [`cointrail/`](cointrail/) | Next.js 15 (App Router), Tailwind 4, Recharts, better-sqlite3 | 8501 (3000 in container) | The dashboard: expenses, cards, recurring charges, food analytics. Proxies to Ingest; keeps only manual entries locally. Optional Capacitor Android wrapper that forwards SMS to Ingest |

```
 phone SMS / Gmail / PDF statements ──▶ ingest :8600 ──(X-API-Key)──▶ cointrail :8501 ──▶ browser
                                         SQLite /data/ingest.db        SQLite /data/finance.db (manual rows)
```

This is a snapshot taken from a private, single-user project and **stripped of all personal data**
(no real SMS, no card numbers, no credentials, no payee names). It has no git history from the original.
Everything runs with synthetic demo data; connecting your own data is optional (see below).

## Quick start (demo data, ~5 minutes)

Requires Docker with Compose v2.

```bash
cp .env.example .env
# set INGEST_API_KEY in .env:   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
mkdir -p data/ingest data/cointrail
cp ingest/data.example/cards.example.json data/ingest/cards.json
docker compose up -d --build
docker compose exec ingest python scripts/seed_demo.py     # ~175 synthetic transactions, 3 cards, 6 months
```

Open <http://localhost:8501> (redirects to `/cards`; the dashboard is at `/expenses`).
Ingest API docs: <http://localhost:8600/docs> (send header `X-API-Key: <your key>` on every other route).

Without Docker:

```bash
# terminal 1 — Ingest
cd ingest && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
export INGEST_DB=$PWD/data/ingest.db CARDS_CONFIG_PATH=$PWD/data/cards.json INGEST_API_KEY=dev-key STATEMENTS_DIR=$PWD/data/statements
mkdir -p data && cp data.example/cards.example.json data/cards.json
python scripts/seed_demo.py && uvicorn main:app --port 8600
# terminal 2 — Cointrail
cd cointrail && npm ci
INGEST_URL=http://localhost:8600 INGEST_API_KEY=dev-key DB_PATH=$PWD/data/finance.db npm run dev   # http://localhost:3000
```

## Hosting it (cloud VM or home server)

* Any Linux box with Docker works (1 vCPU / 1 GB is enough). Use the root `docker-compose.yml`.
* **There is no user login in Cointrail.** Do not expose port 8501 to the open internet. Put it behind a VPN / Tailscale /
  Cloudflare Access / reverse proxy with basic auth. Ingest is protected by `INGEST_API_KEY` (fails closed if unset).
* Publish port 8600 only if you use the Android SMS forwarder or call Ingest from outside; otherwise delete that `ports:` line.
* Persist `./data` (both SQLite databases, Gmail tokens, statement PDFs). Back it up (`sqlite3 ingest.db ".backup out.db"`).

## Using your own data (all optional)

1. **Cards** — edit `data/ingest/cards.json` (bank, last4, name, statement/due day). Cards missing here are not tracked.
2. **SMS** — POST each bank SMS to `POST /sms` with `X-API-Key` (JSON `{"sender": "...", "body": "...", "timestamp": "..."}`).
   Options: any SMS-forwarder app (Tasker / MacroDroid / "SMS Forwarder"), the included Capacitor Android app
   (`cointrail/android`, see [`cointrail/docs/CODE_MAP.md`](cointrail/docs/CODE_MAP.md)), or a one-off import of an
   "SMS Backup & Restore" XML via `POST /sms/import/xml`.
3. **Gmail** — create a Google Cloud OAuth client (Gmail API, read-only scope), save it as
   `data/ingest/gmail_tokens/credentials.json`, then use the `/email` routes to authorise. Needed for card-statement PDFs,
   bank alert e-mails, and Swiggy/Amazon order enrichment.
4. **Statement PDFs** — set `PDF_PASSWORD_<BANK>` in `.env` (see `.env.example`).
5. **Your own payees** — add rules in `ingest/modules/categorizer.py` (see "Personal payee rules") or from the UI
   (Expenses → change category → "apply to all payments to this payee").
6. **Bank wording not parsed?** Add a pattern in `ingest/modules/sms/parser.py` and a sample to
   `ingest/tests/corpus/sms_corpus.jsonl` (format in `ingest/tests/corpus/test_sms_corpus.py`).

## Tests

```bash
cd ingest   && pip install -r requirements-dev.txt && python -m pytest -q      # ~830 tests, no network, throw-away SQLite
cd cointrail && npm ci && npm test                                              # vitest, 264 tests
```
(`cointrail/e2e`, `lighthouserc.json`, `k6/` expect a running instance; set `PLAYWRIGHT_BASE_URL` etc.)

## For AI agents / reviewers

Start with **[`AGENTS.md`](AGENTS.md)** — file map, data flow, invariants, conventions, and where to look for what.
Design rationale is in `ingest/decisions/adr-*.md` (identity-based merge, units, fail-loud parsing, bank numbers win, …).

## Known limitations

* India-only: parsers target HDFC, ICICI, SBI, Axis, HSBC, Pluxee, CRED SMS/e-mail/PDF wording as of 2026.
* Single user, single currency view (INR; foreign amounts are estimated and flagged).
* The public SMS corpus here is a ~57-message subset; the author's full corpus is private.
* Some comments/docs still reference the author's iteration numbers (`iter-18`, `iteration 203`) — historical labels only.
* The Android wrapper's Ingest URL (`SmsBroadcastReceiver.kt`) and WebView URL (`capacitor.config.ts`) are placeholders
  (`*.example.com`); the API key is injected at build time from `INGEST_API_KEY`.
