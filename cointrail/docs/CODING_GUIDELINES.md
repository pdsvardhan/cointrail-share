# Cointrail — Coding Guidelines

> Locked at Stage 2.5 catchup on 2026-05-15. Every Stage 4 change to these
> guidelines requires a new ADR. Source of truth: this file. CLAUDE.md captures
> running iteration history; this file captures durable conventions.

---

## 1. Architectural rules

**Cointrail is pure-frontend + manual-entry over the Ingest service. Never deviate.**

- All transactional data (cards, expenses, statements, recurring, food, SMS) MUST come from the Ingest service at `http://ingest:8600` (Docker network) or `https://ingest.example.com` (Android shell). Ingest decides; Cointrail displays.
- `lib/ingest.ts` is the single client. All `fetch` calls to Ingest go through it. Never call from a component directly.
- Every Ingest call sends `X-API-Key: ${process.env.INGEST_API_KEY}` (server-side only).
- Local SQLite (`data/finance.db`) holds **only**: manual expense entries, food orders, the local seeded categories list, local card metadata for cross-reference, and `settings` KV.
- Cointrail API routes (`app/api/*`) are thin proxies. Add server-side caching only if a route is called >10x per page render.
- **Never** parse SMS / PDF / email inside Cointrail. All parsing lives in Ingest.

## 2. Data layer

- DB is `better-sqlite3` (no drizzle). Connection in `lib/db.ts:getDb()`.
- Pragmas at boot: `WAL`, `foreign_keys ON`, `busy_timeout 5000`.
- Migrations live in `lib/migrate.ts:ensureMigrated()`. Idempotent CREATE-IF-NOT-EXISTS.
- `lib/normalize-date.ts` is mandatory for any date written to the `expenses` table; the migrator backfills non-ISO rows on every boot.
- Schema changes that need ALTER on CHECK constraints: SQLite cannot ALTER CHECK; **recreate the table** (drop → create → copy). Document every such change in an ADR.
- Indexes on `expenses`: `(date)`, `(source)`, `(category)`, `(card_id)`. Add new indexes only with measured benefit.
- Categories are seeded on first run (13 entries in `lib/migrate.ts`). Custom additions allowed; never delete a seed.

## 3. Component conventions

- All cards use the **glass-liquid v1** token contract from `app/globals.css`. Never hand-roll color or backdrop-blur values.
- **Stat cards** use the mouse-tracking 3D tilt primitive (`mouseTrack3D` — to be extracted into a shared util when next touched). Wrapper tilts; content does NOT tilt.
- **3D credit card hover** uses the gist #8 technique: wrapper tilts back ~38° with a cube edge; a physical card replica pops forward as a sibling at z-depth (NOT a child of the tilted wrapper). Reference implementation in `components/cards/`.
- **AnimatePresence** for category card expand/collapse. **layoutId** for active-state morphing across tabs.
- Mobile vertical card carousel uses perspective swipe + dots — not horizontal scroll.
- Theme-aware sections must read CSS vars (`var(--card)`, `var(--text)`, etc.) — never hardcode colors.
- Skeleton states use Tailwind `animate-pulse` on shaded blocks. Never use spinners except for transient async actions.
- Empty states: friendly illustration + CTA. Never show a blank list.

## 4. Theming

- `next-themes` with `attribute="class"` on `<html>` (or `data-theme`, whichever the existing root uses — keep consistent).
- All themable surfaces read CSS variables defined in `app/globals.css`. Light mode is **mandatory** parity for every screen — verify before merge.
- The "system" theme option must follow OS preference.
- Token additions go in `globals.css` under the appropriate scope (`:root` for shared, `[data-theme="dark"]` / `[data-theme="light"]` for mode-specific). Document new tokens in `docs/design-tokens.md`.

## 5. Typography

- **Outfit** for display: large numbers (currency totals, hero values), section labels.
- **Inter** for UI body: tables, buttons, labels, body text.
- **JetBrains Mono** for code blocks, transaction IDs, hashes.
- `tabular-nums` is **mandatory** on every currency value, every stat-card number, every chart axis label.
- Minimum readable size: `text-xs` (12px). Never go smaller.
- Use `font-display: swap` for self-hosted fonts; Google Fonts via `next/font` is the default.

## 6. Numerical formatting

- All ₹ values use `lib/formatters.ts:formatINR()` — never inline `toLocaleString()`.
- Indian grouping: lakhs and crores (e.g. `₹1,23,45,678`), not Western thousands.
- Negative amounts use a leading `-` (no parentheses). Color cue: `var(--text)` (no semantic red unless it's a debt or overdue).
- Percentages use one decimal place by default. Use `formatPercent(n, decimals)` from formatters.

## 7. API route conventions

- Routes live under `app/api/<resource>/`. The folder name should mirror the Ingest endpoint where possible.
- Server handlers must:
  1. Validate query params with a tiny inline check (zod is overkill for simple cases; consider adding zod when a route grows ≥3 fields).
  2. Call Ingest via `lib/ingest.ts`.
  3. Return `NextResponse.json({...})` with the same shape across success/empty.
  4. On Ingest 5xx, return `502` with `{ error: "ingest-unavailable" }` — never bubble Ingest's error JSON unchanged.
- Never expose `Ingest`, `Sonarr`, `Radarr`, or any backend service name in user-facing copy or response payloads.
- POST routes are reserved for state changes (manual expense create/update/delete, classify mapping). Everything else is GET.

## 8. Testing

- `vitest` is the only test framework. Tests live in `__tests__/` mirroring the source path.
- Today's coverage is 3 files (`no-tests` risk, id 6). New features should ship with at least one happy-path test.
- Component tests + E2E (Playwright) are deferred to a future Stage 4 iteration. Don't introduce them piecemeal.
- Mocking the Ingest API is allowed in tests (use `vi.mock('@/lib/ingest')`). Mocking the local SQLite is **not** allowed — use a real in-memory `:memory:` DB.

## 9. Anti-patterns (never do)

- No `localStorage` for user-meaningful state (theme preference is fine; transactions are not).
- No GSAP, no SplitText, no paid animation plugins. motion/react only.
- No SaaS dependencies for security or observability — no Snyk, no Percy, no SaaS Sentry.
- No surfacing of `Ingest`, `Sonarr`, `Radarr`, `MacroDroid` etc. in user copy.
- No half-built features. Sunset OR build, no middle ground.
- No introducing an auth layer in Cointrail. Network-bound is the contract (per Doc 1.non_goals + risk no-auth accepted).
- No real-time websockets. Polling is the chosen freshness model.
- No `ALTER TABLE ... ADD CHECK` (SQLite limitation — recreate the table).

## 10. Capacitor / Android shell

- Package id: `com.example.cointrail`. Never change without an ADR.
- WebView loads `https://cointrail.example.com`. Offline fallback: `offline.html` bundled in apk assets.
- Native SMS receiver listens for `android.provider.Telephony.SMS_RECEIVED` and POSTs JSON to `https://ingest.example.com/sms` with `X-API-Key`. Never POST to Cointrail itself; Ingest is the SMS sink.
- Auto-versioning at build time:
  - `versionCode = $(git rev-list --count HEAD)` — monotonic, collision-free.
  - `versionName` = stripped from `android-v*` git tag (e.g. `android-v0.2.0` → `0.2.0`).
- Build runs in Gitea Actions on the self-hosted runner; uses shared keystore at `/mnt/storage/android-signing/`. Never commit signing keys.
- APK + manifest published to `https://builds.example.com/cointrail-latest.apk` and `/cointrail.json` (Obtainium-readable). Cloudflare cache: `no-store` on HTML/JSON; 1h on APK.
- New native plugins require an ADR and an architecture-diagram update.
