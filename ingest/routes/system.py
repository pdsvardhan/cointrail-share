"""System routes: health, stats, digest, cron, aliases, enrichments, UPI classification."""
import os
import re
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from typing import Optional

import config
from db import get_db, wal_size_bytes
from modules.sms.receiver import get_sms_stats
from modules.cross_matcher import run_cross_match, get_duplicate_stats
from modules.categorizer import categorize
from util.timez import IST, now_ist, today_ist

router = APIRouter()

# A transaction-capture gap longer than this flips /health to 'degraded'
# (SMS alerts normally arrive ~daily). Tunable.
STALE_DAYS_THRESHOLD = 3

# --- Multi-signal thresholds (iter-16, 2026-08-20) ---
# Why this section exists: on 2026-08-12 the task queue wedged and stayed wedged for
# seven days while /health reported "ok" every six hours. It looked healthy because the
# ONLY data signal it checked was transaction staleness — and realtime SMS posts straight
# to /sms, bypassing the queue entirely, so transactions kept arriving daily while email
# sync, PDF parsing, enrichment, cross-matching and food parsing were all dead.
#
# One live channel masked four dead ones. The fix is to check each channel on its own,
# plus the queue's own liveness, so no single healthy path can vouch for the rest.
QUEUE_DEPTH_THRESHOLD = 20        # pending tasks; hourly cadence means >20 is a backlog
OLDEST_PENDING_HOURS = 2          # a task waiting this long means nothing is draining
TASK_TYPE_STALE_HOURS = 3         # each hourly task type must succeed this often
# Task types that are not hourly (iter-18 HP6). Without this the 09:00 IST daily_digest would
# read as "has not succeeded in 4h" by noon and flip /health to degraded every day.
TASK_STALE_HOURS_BY_TYPE = {"daily_digest": 26}
WAL_SIZE_WARN_BYTES = 64 * 1024 * 1024   # WAL hit 22MB while the lock was held

# Per-channel freshness. Each entry is checked independently.
#   key -> (human label, SQL returning one MAX(timestamp) column, max age in hours)
CHANNEL_CHECKS = {
    "sms": (
        "SMS capture",
        "SELECT MAX(received_at) AS t FROM sms",
        24,
    ),
    "email": (
        "Email sync",
        "SELECT MAX(synced_at) AS t FROM emails",
        6,
    ),
    "pdf": (
        "Statement parsing",
        # Deliberately email_attachments, not statements: statements.parsed_at is NULL
        # on all 85 rows (never populated by the parser — tracked as part of C1), so
        # reading it produced a permanently null signal that could never alarm. Attachment
        # parse time is the honest measure of "is the PDF path doing work".
        "SELECT MAX(parsed_at) AS t FROM email_attachments WHERE is_parsed = 1",
        24 * 40,   # statements are monthly; only alarm if a whole cycle is missed
    ),
    "food": (
        "Food invoice parsing",
        "SELECT MAX(created_at) AS t FROM food_invoice_orders",
        24 * 14,   # order-frequency dependent; a fortnight of silence is suspicious
    ),
}


def _age_hours(conn, ts):
    """Age in hours of a timestamp string, or None if absent/unparseable."""
    if not ts:
        return None
    row = conn.execute(
        "SELECT (julianday('now') - julianday(?)) * 24.0 AS h", (ts,)
    ).fetchone()
    return round(row["h"], 1) if row and row["h"] is not None else None


def _hours_since(conn, sql: str):
    """Age in hours of the MAX(timestamp) returned by sql, or None if there is no row.

    Wrapped in try/except because a health check must never 500: a missing table (an
    older DB, a half-run migration) should degrade one signal, not the whole endpoint.
    """
    try:
        row = conn.execute(sql).fetchone()
    except Exception:
        return None
    return _age_hours(conn, row["t"] if row else None)


# --- Correctness (iter-18 HP6 / M16) ---
# Freshness alone stayed green while 6 of 7 October Axis spends vanished (a new bank wording),
# spend was double counted and two cards showed a due date later than the bank's. These checks
# ask whether the data is RIGHT, not just recent. They produce `warnings`, never `issues`: the
# 6-hourly freshness cron pages on status != 'ok', and a warning is a to-do for the owner (add a
# pattern, look at a statement), not an outage. The daily digest (modules/digest.py) repeats them.
#
# /health is public, so the block carries counts and bank names only — no card numbers, no
# message text. The authenticated /digest shows the detail.
RECEIPT_STALE_HOURS = 12        # the phone forwarder posts several times a day
BACKUP_STALE_HOURS = 36         # daily phone backup + slack; ops/ingest-sms-import.sh uses the same
UNACCOUNTED_WINDOW_DAYS = 7
CURRENT_CYCLE_DAYS = 45         # same window as /cards/summary `current_cycle` (contract 3)
REVIEW_RECENT_DAYS = 90         # older mismatches predate the transaction history; they can never
                                # reconcile and would turn the warning into permanent noise

# Statement / bill notices name an amount and often a verb ("Statement is sent to ..."), so they
# look like money movement. Requiring "statement" near "due"/"total" keeps a refund that merely
# mentions a revised due ("Reversal of Rs 4.01 credited ... Revised total due") counted as a drop.
_STATEMENT_NOTICE_RE = re.compile(
    r"statement.{0,200}?(?:due|total)|(?:due|total).{0,200}?statement", re.I | re.S)
# An OTP is never a completed transaction, but Axis' "NNNNNN is SECRET OTP for txn of INR ..."
# names an amount and "txn"; 15 of them sat among the audit's "silently dropped" Axis SMS.
_OTP_RE = re.compile(r"\bOTP\b", re.I)
_BACKUP_NAME_RE = re.compile(r"sms-(\d{14})")


def _by_bank_text(by_bank):
    return ", ".join(f"{bank} {n}" for bank, n in sorted(by_bank.items(), key=lambda kv: (-kv[1], kv[0])))


def _check_quarantine(conn):
    from modules.quarantine import summary
    q = summary(conn, limit=1)
    oldest = conn.execute(
        "SELECT MIN(first_seen) AS t FROM parse_quarantine WHERE resolved_at IS NULL"
    ).fetchone()["t"]
    block = {"open": q["open"], "by_bank": q["by_bank"], "oldest_hours": _age_hours(conn, oldest)}
    warning = None
    if q["open"]:
        warning = (
            f"{q['open']} bank message(s) in quarantine — they carry money but no rule could read them "
            f"({_by_bank_text(q['by_bank'])}; oldest {block['oldest_hours']}h). A bank wording is new or "
            f"changed: add a pattern, then retry the quarantine."
        )
    return block, warning


def _is_bill_or_noise(body):
    """Known non-transaction outcomes a transaction-looking SMS may legitimately end in:
    noise (OTP, declined, due reminders...), a card bill payment, or a statement notice."""
    from modules.sms.receiver import is_non_transaction
    from services.cards_service import parse_payment_sms
    return bool(is_non_transaction(body) or _OTP_RE.search(body) or parse_payment_sms(body)
                or _STATEMENT_NOTICE_RE.search(body))


def _check_unaccounted_sms(conn):
    """Bank SMS that look like money movement, were marked processed in the window, and ended
    in nothing: no transaction, no quarantine row, no bill payment, not known noise. This is the
    exact failure that hid the Axis wording change (iter-18 HP1); after HP1 it must be 0."""
    from modules.quarantine import bank_hint, looks_like_money_movement
    from modules.sms.processing import classify_sms_row
    rows = conn.execute(
        "SELECT s.id, s.sender, s.body, s.phone_timestamp, s.received_at FROM sms s "
        "WHERE s.is_processed = 1 AND s.received_at >= datetime('now', ?) "
        "AND NOT EXISTS (SELECT 1 FROM transactions t "
        "                WHERE t.source = 'sms' AND t.source_ref = CAST(s.id AS TEXT)) "
        "AND NOT EXISTS (SELECT 1 FROM parse_quarantine q "
        "                WHERE q.source = 'sms' AND q.source_ref = CAST(s.id AS TEXT)) "
        "AND NOT EXISTS (SELECT 1 FROM cc_payments p WHERE p.sms_id = s.id)",
        (f"-{UNACCOUNTED_WINDOW_DAYS} days",),
    ).fetchall()
    by_bank = {}
    for r in rows:
        body = r["body"] or ""
        bank = bank_hint(r["sender"], None)      # a bank SENDER, not a merchant naming a bank
        if not bank or not looks_like_money_movement(body) or _is_bill_or_noise(body):
            continue
        # Same judgement the pipeline itself makes (modules.sms.processing): only a message the
        # classifier says is a transaction or belongs in quarantine counts as dropped. Statement
        # alerts and noise wordings the classifier knows about are legitimate endings.
        if classify_sms_row(conn, dict(r))["class"] not in ("transaction", "quarantined"):
            continue
        by_bank[bank] = by_bank.get(bank, 0) + 1
    count = sum(by_bank.values())
    block = {"count": count, "by_bank": by_bank, "window_days": UNACCOUNTED_WINDOW_DAYS}
    warning = None
    if count:
        warning = (
            f"{count} transaction-looking bank SMS in the last {UNACCOUNTED_WINDOW_DAYS} days were dropped "
            f"silently — no transaction, no quarantine ({_by_bank_text(by_bank)})."
        )
    return block, warning


def _check_statements_review(conn):
    cutoff = (today_ist() - timedelta(days=REVIEW_RECENT_DAYS)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN billing_end >= ? THEN 1 ELSE 0 END) AS recent "
        "FROM statements WHERE needs_review = 1",
        (cutoff,),
    ).fetchone()
    total, recent = row["total"] or 0, row["recent"] or 0
    block = {"recent": recent, "total": total, "recent_days": REVIEW_RECENT_DAYS}
    warning = None
    if recent:
        warning = (
            f"{recent} statement(s) from the last {REVIEW_RECENT_DAYS} days need review: the bank's printed "
            f"total and the parsed lines disagree ({total} flagged in all)."
        )
    return block, warning


def _latest_statement(conn, card_id):
    return conn.execute(
        "SELECT billing_end, due_date, due_date_source FROM statements WHERE card_id = ? "
        "ORDER BY billing_end IS NULL, billing_end DESC, year DESC, month DESC LIMIT 1",
        (card_id,),
    ).fetchone()


def _check_due_dates(conn):
    """Active cards in their current cycle whose latest statement has no bank due date. Two cards
    showed a due date 2-4 days LATER than the bank's (iter-18 H1) — a late fee if trusted. A dormant
    card's old statement is skipped: its due date has passed and cannot cost anything now."""
    cutoff = (today_ist() - timedelta(days=CURRENT_CYCLE_DAYS)).isoformat()
    by_bank, checked = {}, 0
    for card in conn.execute("SELECT id, bank FROM cards WHERE active = 1").fetchall():
        latest = _latest_statement(conn, card["id"])
        if not latest or not latest["billing_end"] or latest["billing_end"] < cutoff:
            continue
        checked += 1
        if latest["due_date_source"] != "bank" or not latest["due_date"]:
            by_bank[card["bank"]] = by_bank.get(card["bank"], 0) + 1
    count = sum(by_bank.values())
    block = {"estimated": count, "current_cycle_cards": checked, "by_bank": by_bank}
    warning = None
    if count:
        warning = (
            f"{count} of {checked} current-cycle card(s) show an estimated due date, not the bank's "
            f"({_by_bank_text(by_bank)}) — the statement SMS/PDF due date was not captured."
        )
    return block, warning


def _check_sms_receipts(conn):
    age = _hours_since(conn, "SELECT MAX(received_at) AS t FROM sms_receipts")
    block = {"hours_since_last": age, "limit_hours": RECEIPT_STALE_HOURS}
    warning = None
    if age is not None and age > RECEIPT_STALE_HOURS:
        warning = (
            f"No SMS has reached Ingest from the phone forwarder for {age}h (limit {RECEIPT_STALE_HOURS}h) "
            f"— check the SMS forwarder app on the phone."
        )
    return block, warning


def _check_sms_backup(conn):
    """Age of the newest phone SMS backup imported by ops/ingest-sms-import.sh (iter-18 H7). The
    marker's mtime is when a NEW backup was last imported; its content names the backup file."""
    path = config.SMS_BACKUP_MARKER
    if not path or not os.path.isfile(path):
        # iteration 203 E13: an absent safety net used to be reported as "configured: false" with no
        # warning, so it could be missing without anyone noticing
        return {"configured": False}, (
            "The SMS backup safety net is not set up (no phone backup has ever been imported) — the "
            "realtime forwarder is the only capture path for accounts that only send SMS. See ops/README.md.")
    imported = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
    now = datetime.now(timezone.utc)
    imported_hours = round((now - imported).total_seconds() / 3600.0, 1)
    with open(path, encoding="utf-8", errors="replace") as f:
        name = f.read().strip()
    backup_hours = None
    m = _BACKUP_NAME_RE.search(name)
    if m:
        # SMS Backup & Restore names files by the phone's local (IST) clock.
        taken = datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=IST)
        backup_hours = round((now - taken).total_seconds() / 3600.0, 1)
    block = {
        "configured": True,
        "last_backup": name[:80],
        "imported_hours": imported_hours,
        "backup_age_hours": backup_hours,
        "limit_hours": BACKUP_STALE_HOURS,
    }
    warning = None
    if imported_hours > BACKUP_STALE_HOURS:
        since = imported.astimezone(IST).strftime("%d %b %Y")
        warning = (
            f"The SMS backup safety net has not imported a new phone backup since {since} "
            f"({imported_hours}h, limit {BACKUP_STALE_HOURS}h) — the realtime forwarder is the only "
            f"capture path. See ops/README.md."
        )
    return block, warning


def _check_cancelled_recurring(conn):
    """iter-203 E5: a series the owner marked cancelled that charged again after the date. Counts
    only here (/health is public); the digest names the merchant and the charge."""
    from services.recurring_service import cancelled_series_charged_again, list_cancellations
    again = cancelled_series_charged_again(conn)
    block = {"cancelled": len(list_cancellations(conn)), "charged_again": len(again)}
    warning = None
    if again:
        warning = (
            f"{len(again)} subscription(s) charged again after you cancelled it — see the daily digest "
            f"or the Recurring page."
        )
    return block, warning


# --- Silent accounts (iteration 203 E13) ---
# HSBC accounts (Rs 47.8 lakh of 2026 debits) reach Ingest only by SMS: no alert emails, no statement
# PDFs. A forwarder that stops for one phone, or a bank that changes its sender, loses them for good,
# and the 12-hour forwarder check cannot see it while other SMS keep arriving. Each account is held to
# its OWN rhythm: silent once its last transaction is older than its usual gap.
SILENT_LOOKBACK_DAYS = 180
SILENT_MIN_DAYS_SEEN = 6        # fewer active days in the window: no usual gap to speak of
SILENT_MIN_GAP_DAYS = 3
SILENT_DORMANT_DAYS = 60        # quiet this long: a closed card or an unused account, not an outage


def _tail_match(a, b):
    a, b = str(a or ""), str(b or "")
    if len(a) >= 4 and len(b) >= 4:
        return a[-4:] == b[-4:]
    return len(a) >= 3 and len(b) >= 3 and a[-3:] == b[-3:]


def silent_accounts(conn, today=None):
    """Every active card and own account with a usual rhythm, and whether it has gone quiet.

    Usual gap = max(3 days, 3 x the median gap, the 95th-percentile gap) between the days it had a
    transaction (any source, copies included) in the last 180 days. Returns a list of dicts
    {bank, last4, name, last_seen, usual_gap_days, silent_days, state: 'silent'|'ok'|'dormant'|'sparse'}."""
    today = today or today_ist()
    since = (today - timedelta(days=SILENT_LOOKBACK_DAYS)).isoformat()
    numbers = [r["n"] for r in conn.execute(
        "SELECT DISTINCT card_last4 AS n FROM transactions WHERE date >= ? AND card_last4 IS NOT NULL "
        "AND TRIM(card_last4) <> ''", (since,))]
    out = []
    for card in conn.execute(
            "SELECT bank, last4, card_name, pdf_card_last4, active FROM cards "
            "WHERE replaced_by_card_id IS NULL ORDER BY bank, last4").fetchall():
        own = [n for n in numbers if _tail_match(n, card["last4"])
               or (card["pdf_card_last4"] and n == card["pdf_card_last4"])]
        if not own:
            continue
        days = sorted({date.fromisoformat(r["d"][:10]) for r in conn.execute(
            f"SELECT DISTINCT date AS d FROM transactions WHERE date >= ? AND date <= ? "
            f"AND card_last4 IN ({','.join('?' * len(own))})", (since, today.isoformat(), *own))})
        entry = {"bank": card["bank"], "last4": card["last4"], "name": card["card_name"],
                 "last_seen": days[-1].isoformat() if days else None, "usual_gap_days": None,
                 "silent_days": (today - days[-1]).days if days else None}
        if len(days) < SILENT_MIN_DAYS_SEEN:
            entry["state"] = "sparse"
        else:
            gaps = sorted((b - a).days for a, b in zip(days, days[1:]))
            median = gaps[len(gaps) // 2]
            p95 = gaps[min(len(gaps) - 1, int(round(0.95 * (len(gaps) - 1))))]
            entry["usual_gap_days"] = max(SILENT_MIN_GAP_DAYS, 3 * median, p95)
            if entry["silent_days"] > SILENT_DORMANT_DAYS:
                entry["state"] = "dormant"
            elif entry["silent_days"] > entry["usual_gap_days"]:
                entry["state"] = "silent"
            else:
                entry["state"] = "ok"
        out.append(entry)
    return out


def _check_silent_accounts(conn):
    rows = silent_accounts(conn)
    silent = [r for r in rows if r["state"] == "silent"]
    by_bank = {}
    for r in silent:
        by_bank[r["bank"]] = by_bank.get(r["bank"], 0) + 1
    block = {"silent": len(silent), "by_bank": by_bank,
             "checked": sum(1 for r in rows if r["state"] in ("ok", "silent")),
             "dormant": sum(1 for r in rows if r["state"] == "dormant")}
    warning = None
    if silent:
        worst = max(silent, key=lambda r: r["silent_days"])
        warning = (
            f"{len(silent)} account(s) that normally report have gone quiet for longer than usual "
            f"({_by_bank_text(by_bank)}; longest {worst['silent_days']} days against a usual gap of "
            f"{worst['usual_gap_days']}) — check the SMS forwarder and the bank's alerts; the digest names them."
        )
    return block, warning


CORRECTNESS_CHECKS = (
    ("quarantine", _check_quarantine),
    ("unaccounted_sms", _check_unaccounted_sms),
    ("statements_needs_review", _check_statements_review),
    ("due_date_estimated", _check_due_dates),
    ("sms_receipts", _check_sms_receipts),
    ("sms_backup", _check_sms_backup),
    ("cancelled_recurring", _check_cancelled_recurring),
    ("silent_accounts", _check_silent_accounts),
)


def correctness_checks(conn):
    """Run every correctness check. Returns (block, warnings) where warnings is a list of
    (check_key, human line). A check that raises (missing table on an old DB, unreadable marker)
    becomes its own warning instead of breaking /health — a health check must never 500, and a
    check that silently stopped running is the failure mode this iteration is about."""
    block, warnings = {}, []
    for key, check in CORRECTNESS_CHECKS:
        try:
            block[key], warning = check(conn)
        except Exception as e:
            block[key] = {"error": f"{type(e).__name__}: {e}"}
            warning = f"Correctness check '{key}' failed: {type(e).__name__}: {e}"
        if warning:
            warnings.append((key, warning))
    return block, warnings


@router.get("/health")
def health():
    """Liveness + DATA health + correctness (iter-18 HP6). Public; see health_report."""
    return health_report(get_db())


def health_report(conn):
    """Liveness + DATA health. Returns status='degraded' (not just 'ok') when the
    pipeline has recent failures, a task is stuck in 'running', or transaction
    capture has gone stale. The 2026 7-week stall went unnoticed precisely because
    /health was unconditionally 'ok' — this makes silent data loss observable.

    `correctness` + `warnings` (iter-18 HP6) report whether the data is right; they never change
    `status` or `issues`, so the freshness cron keeps paging only on outages."""
    issues = []

    fails = conn.execute(
        "SELECT task_type, COUNT(*) AS c FROM task_queue "
        "WHERE status='failed' AND COALESCE(completed_at, created_at) >= datetime('now','-1 day') "
        "GROUP BY task_type"
    ).fetchall()
    fail_total = sum(r["c"] for r in fails)
    if fail_total:
        issues.append("task failures (24h): " + ", ".join(f"{r['task_type']}x{r['c']}" for r in fails))

    stuck = conn.execute("SELECT COUNT(*) AS c FROM task_queue WHERE status='running'").fetchone()["c"]
    if stuck:
        issues.append(f"{stuck} task(s) stuck in 'running'")

    row = conn.execute("SELECT MAX(date) AS md FROM transactions WHERE is_duplicate = 0").fetchone()
    last_txn = row["md"] if row and row["md"] else None
    stale_days = None
    if last_txn:
        stale_days = conn.execute(
            "SELECT CAST(julianday('now') - julianday(?) AS INT) AS d", (last_txn,)
        ).fetchone()["d"]
        if stale_days is not None and stale_days > STALE_DAYS_THRESHOLD:
            issues.append(f"no new transaction in {stale_days}d (last {last_txn[:10]})")

    # --- Queue liveness (iter-16) ---
    # A backlog that only grows is the signature of a wedged runner. Depth alone is
    # ambiguous during a genuine burst, so age of the oldest pending task is the
    # stronger signal: nothing should sit pending for hours on an hourly cadence.
    qrow = conn.execute(
        "SELECT COUNT(*) AS depth, MIN(created_at) AS oldest "
        "FROM task_queue WHERE status = 'pending'"
    ).fetchone()
    queue_depth = qrow["depth"] if qrow else 0
    oldest_pending_hours = _age_hours(conn, qrow["oldest"] if qrow else None)
    if queue_depth > QUEUE_DEPTH_THRESHOLD:
        issues.append(f"queue backlog: {queue_depth} pending tasks")
    if oldest_pending_hours is not None and oldest_pending_hours > OLDEST_PENDING_HOURS:
        issues.append(f"oldest pending task is {oldest_pending_hours}h old — queue not draining")

    try:
        from services.queue import queue_health
        qh = queue_health()
        if not qh.get("healthy"):
            issues.append(
                f"task runner unhealthy: {qh.get('consecutive_failures')} consecutive "
                f"failures ({qh.get('last_error')})"
            )
    except Exception:
        qh = {}

    # --- Per-task-type staleness (iter-16) ---
    # The whole point: each recurring job must have succeeded recently *on its own*.
    # Previously a single fresh channel made the entire pipeline look alive.
    last_ok = {
        r["task_type"]: r["last"]
        for r in conn.execute(
            "SELECT task_type, MAX(completed_at) AS last FROM task_queue "
            "WHERE status='completed' GROUP BY task_type"
        ).fetchall()
    }
    task_ages = {}
    for task_type, last in last_ok.items():
        age = _age_hours(conn, last)
        task_ages[task_type] = age
        limit = TASK_STALE_HOURS_BY_TYPE.get(task_type, TASK_TYPE_STALE_HOURS)
        if age is not None and age > limit:
            issues.append(f"{task_type} has not succeeded in {age}h")

    # --- Per-channel data freshness (iter-16) ---
    channels = {}
    for key, (label, sql, max_age_h) in CHANNEL_CHECKS.items():
        age = _hours_since(conn, sql)
        channels[key] = age
        if age is not None and age > max_age_h:
            issues.append(f"{label} stale: {age}h (limit {max_age_h}h)")

    # --- Storage (iter-16) ---
    wal_bytes = wal_size_bytes()
    if wal_bytes > WAL_SIZE_WARN_BYTES:
        issues.append(f"WAL is {wal_bytes // (1024 * 1024)}MB — checkpointing may be blocked")

    correctness, warnings = correctness_checks(conn)

    return {
        "status": "degraded" if issues else "ok",
        "service": "ingest",
        "last_transaction": last_txn[:10] if last_txn else None,
        "transaction_staleness_days": stale_days,
        "task_failures_24h": fail_total,
        "stuck_running": stuck,
        "queue": {
            "pending_depth": queue_depth,
            "oldest_pending_hours": oldest_pending_hours,
            "consecutive_failures": qh.get("consecutive_failures"),
            "runner_healthy": qh.get("healthy"),
        },
        "task_success_age_hours": task_ages,
        "channel_freshness_hours": channels,
        "wal_bytes": wal_bytes,
        "issues": issues,
        "correctness": correctness,
        "warnings": [line for _key, line in warnings],
    }


@router.get("/digest")
def get_digest():
    """The daily digest text (iter-18 HP6): dues, quarantine, statements to review, warnings.
    Authenticated (not in PUBLIC_PATHS) — unlike /health it names cards (last 4 only)."""
    from modules.digest import build_digest
    return {"generated_at": now_ist().isoformat(timespec="seconds"), "text": build_digest(get_db())}


@router.post("/digest/send")
def send_digest():
    """Build the digest and push it to the configured notification channels."""
    from modules.digest import send_daily_digest
    return send_daily_digest(get_db(), include_text=True)


@router.get("/stats")
def overall_stats():
    sms = get_sms_stats()
    dupes = get_duplicate_stats()
    conn = get_db()
    txn_count = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE is_duplicate = 0").fetchone()["c"]
    uncategorized = conn.execute(
        "SELECT COUNT(*) as c FROM transactions WHERE is_duplicate = 0 AND category = 'Uncategorized'"
    ).fetchone()["c"]
    recent_fails = conn.execute(
        "SELECT COUNT(*) as c FROM task_queue WHERE status='failed' "
        "AND COALESCE(completed_at, created_at) >= datetime('now','-1 day')"
    ).fetchone()["c"]
    last_success = conn.execute(
        "SELECT task_type, MAX(completed_at) as last FROM task_queue "
        "WHERE status='completed' GROUP BY task_type"
    ).fetchall()
    last_txn = conn.execute(
        "SELECT MAX(date) as md FROM transactions WHERE is_duplicate = 0"
    ).fetchone()["md"]
    return {
        "sms": sms, "transactions": txn_count,
        "uncategorized": uncategorized,
        "uncategorized_pct": round(uncategorized / txn_count * 100, 1) if txn_count > 0 else 0,
        "duplicates": dupes,
        "last_transaction": last_txn[:10] if last_txn else None,
        "pipeline": {
            "recent_failures_24h": recent_fails,
            "last_success_by_type": {r["task_type"]: r["last"] for r in last_success},
        },
    }


@router.post("/cross-match")
def cross_match():
    return run_cross_match()


@router.get("/aliases")
def list_aliases():
    conn = get_db()
    rows = conn.execute("SELECT * FROM merchant_aliases ORDER BY category, merchant_name").fetchall()
    return {"aliases": [dict(r) for r in rows], "total": len(rows)}


@router.post("/aliases")
def add_alias(data: dict):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO merchant_aliases (pattern, match_type, merchant_name, category, source, notes) "
            "VALUES (?, ?, ?, ?, 'manual', ?)",
            (data["pattern"], data.get("match_type", "contains"), data["merchant_name"],
             data["category"], data.get("notes"))
        )
        conn.commit()
        return {"status": "created"}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/categories")
def list_categories():
    conn = get_db()
    rows = conn.execute(
        "SELECT category, COUNT(*) as count, SUM(CASE WHEN txn_type='debit' THEN amount ELSE 0 END) as total_spend "
        "FROM transactions WHERE is_duplicate = 0 GROUP BY category ORDER BY total_spend DESC"
    ).fetchall()
    return {"categories": [dict(r) for r in rows]}


@router.get("/categories/summary")
def category_summary(
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
):
    conn = get_db()
    query = (
        "SELECT category, COUNT(*) as count, "
        "SUM(CASE WHEN txn_type='debit' THEN amount ELSE 0 END) as total_spend "
        "FROM transactions WHERE is_duplicate = 0 AND txn_type = 'debit'"
    )
    params = []
    if date_from:
        query += " AND date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND date <= ?"
        params.append(date_to)
    query += " GROUP BY category ORDER BY total_spend DESC"
    rows = conn.execute(query, params).fetchall()
    return {"categories": [dict(r) for r in rows]}


@router.get("/analysis/monthly")
def monthly_analysis():
    conn = get_db()
    rows = conn.execute(
        "SELECT strftime('%Y-%m', date) as month, COUNT(*) as txn_count, "
        "SUM(CASE WHEN txn_type='debit' THEN amount ELSE 0 END) as total_spend, "
        "SUM(CASE WHEN txn_type='credit' THEN amount ELSE 0 END) as total_income "
        "FROM transactions WHERE is_duplicate = 0 GROUP BY month ORDER BY month DESC"
    ).fetchall()
    return {"months": [dict(r) for r in rows]}


@router.get("/analysis/merchants")
def top_merchants(limit: int = 20):
    conn = get_db()
    rows = conn.execute(
        "SELECT merchant, COUNT(*) as txn_count, SUM(amount) as total_spend "
        "FROM transactions WHERE is_duplicate = 0 AND txn_type = 'debit' "
        "GROUP BY merchant ORDER BY total_spend DESC LIMIT ?", (limit,)
    ).fetchall()
    return {"merchants": [dict(r) for r in rows]}


@router.get("/enrichments")
def list_enrichments(brand: Optional[str] = None, enrichment_type: Optional[str] = None):
    conn = get_db()
    query = ("SELECT ee.*, e.subject, e.sender_email, e.date as email_date "
             "FROM email_enrichments ee LEFT JOIN emails e ON ee.email_id = e.id WHERE 1=1")
    params = []
    if brand:
        query += " AND ee.brand = ?"
        params.append(brand)
    if enrichment_type:
        query += " AND ee.enrichment_type = ?"
        params.append(enrichment_type)
    query += " ORDER BY ee.matched_at DESC LIMIT 200"
    rows = conn.execute(query, params).fetchall()
    return {"enrichments": [dict(r) for r in rows], "total": len(rows)}


@router.post("/enrichment/run")
def trigger_enrichment():
    from modules.email.enrichment import run_enrichment
    return run_enrichment()


@router.get("/upi/unclassified")
def get_unclassified_upi():
    conn = get_db()
    rows = conn.execute(
        "SELECT t.id, t.amount, t.date, t.raw_description, t.merchant, t.category, t.source "
        "FROM transactions t "
        "LEFT JOIN upi_mappings um ON (t.raw_description LIKE '%' || um.upi_id || '%' "
        "OR t.raw_description LIKE '%' || um.name_pattern || '%') "
        "WHERE t.source = 'sms' AND t.raw_description LIKE '%UPI%' "
        "AND um.id IS NULL AND t.is_duplicate = 0 AND t.txn_type = 'debit' "
        "ORDER BY t.date DESC"
    ).fetchall()
    return {"transactions": [dict(r) for r in rows], "total": len(rows)}


@router.post("/upi/classify")
def classify_upi(data: dict):
    txn_id = data.get("transaction_id")
    mapped_type = data.get("type", "merchant")
    brand = data.get("brand")
    category = data.get("category")
    upi_id = data.get("upi_id")
    name_pattern = data.get("name_pattern")

    conn = get_db()
    if upi_id or name_pattern:
        try:
            conn.execute(
                "INSERT OR REPLACE INTO upi_mappings (upi_id, name_pattern, mapped_type, brand, category) "
                "VALUES (?, ?, ?, ?, ?)",
                (upi_id, name_pattern, mapped_type, brand, category)
            )
        except Exception:
            pass
    if brand and category:
        conn.execute(
            "UPDATE transactions SET merchant = ?, category = ?, confidence = 'manual' WHERE id = ?",
            (brand, category, txn_id)
        )
    conn.commit()
    return {"status": "classified", "transaction_id": txn_id}


@router.post("/upi/run")
def run_upi_pass():
    """Seed upi_mappings from resolved transactions, then apply them (iter-16 C9)."""
    from modules.upi import run_upi_classification
    return run_upi_classification()


@router.get("/upi/payees")
def upi_payees(limit: int = Query(40, le=200)):
    """Un-named UPI payees ranked by value — the human-labelling queue (iter-16 C9).

    Person-to-person VPAs are intentionally never auto-named; there is nothing in the
    data that identifies a person, and guessing would fabricate confidence.
    """
    from modules.upi import unmapped_payees
    return unmapped_payees(limit=limit)


@router.get("/upi/mappings")
def list_upi_mappings():
    conn = get_db()
    rows = conn.execute("SELECT * FROM upi_mappings ORDER BY created_at DESC").fetchall()
    return {"mappings": [dict(r) for r in rows], "total": len(rows)}
