"""
SQLite-based background task queue.
No external dependencies — fits single-user model.
"""
import json
import threading
import time
import traceback
from datetime import datetime

from db import get_db

MAX_RETRIES = 3
POLL_INTERVAL = 5  # seconds
STUCK_TIMEOUT_MINUTES = 30  # a 'running' task older than this is treated as hung

# --- Failure handling (iter-16, 2026-08-20) ---
# The old loop caught every exception, slept a fixed 5s and retried forever, with no
# rollback and no escalation. During the 2026-08-12..19 lock-up that produced 103,713
# identical log lines and not one alert. Now: back off, and expose the failure streak
# so /health can actually see a wedged queue.
MAX_BACKOFF = 60          # seconds — ceiling for consecutive-failure backoff
UNHEALTHY_AFTER = 10      # consecutive failures before the queue reports unhealthy
SWEEP_INTERVAL = 60       # seconds — sweep_stuck_running() ran on EVERY idle poll,
                          # i.e. a write attempt every 5s forever even when idle

_consecutive_failures = 0
_last_error = None
_last_sweep = 0.0


def queue_health() -> dict:
    """Queue-side health signals. Consumed by /health so a wedged runner is visible."""
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) AS depth, MIN(created_at) AS oldest "
        "FROM task_queue WHERE status = 'pending'"
    ).fetchone()
    depth = row["depth"] if row else 0
    oldest = row["oldest"] if row else None
    stale = conn.execute(
        "SELECT task_type, MAX(completed_at) AS last_ok FROM task_queue "
        "WHERE status = 'completed' GROUP BY task_type"
    ).fetchall()
    return {
        "pending_depth": depth,
        "oldest_pending": oldest,
        "consecutive_failures": _consecutive_failures,
        "last_error": _last_error,
        "healthy": _consecutive_failures < UNHEALTHY_AFTER,
        "last_success_by_type": {r["task_type"]: r["last_ok"] for r in stale},
    }


def init_queue_table():
    """Create the task_queue table if it doesn't exist."""
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_queue (
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
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_status ON task_queue(status)")
    conn.commit()


def enqueue(task_type: str, payload: dict = None, coalesce: bool = False) -> int:
    """Add a task to the queue. Returns task ID (or the existing one when coalescing).

    coalesce=True skips the insert when a pending task of the same type already exists.
    The recurring hourly jobs are idempotent — each one processes everything outstanding
    — so a second pending copy does no additional work. Without this guard the 7 hourly
    tasks were re-enqueued blindly for 169 hours during the 2026-08 stall and built a
    1,183-task backlog that hid the real failure.
    """
    conn = get_db()
    if coalesce:
        existing = conn.execute(
            "SELECT id FROM task_queue WHERE task_type = ? AND status = 'pending' "
            "ORDER BY id ASC LIMIT 1",
            (task_type,)
        ).fetchone()
        if existing:
            return existing["id"]
    cur = conn.execute(
        "INSERT INTO task_queue (task_type, payload) VALUES (?, ?)",
        (task_type, json.dumps(payload) if payload else None)
    )
    conn.commit()
    return cur.lastrowid


def get_task(task_id: int) -> dict:
    """Get task by ID."""
    conn = get_db()
    row = conn.execute("SELECT * FROM task_queue WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return None
    task = dict(row)
    if task.get("payload"):
        try:
            task["payload"] = json.loads(task["payload"])
        except (json.JSONDecodeError, TypeError):
            pass
    if task.get("result"):
        try:
            task["result"] = json.loads(task["result"])
        except (json.JSONDecodeError, TypeError):
            pass
    return task


def list_tasks(limit: int = 20) -> list:
    """List recent tasks."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, task_type, status, created_at, started_at, completed_at, retry_count "
        "FROM task_queue ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# --- Task implementations ---

TASK_HANDLERS = {}


def register_handler(task_type: str):
    """Decorator to register a task handler."""
    def decorator(func):
        TASK_HANDLERS[task_type] = func
        return func
    return decorator


@register_handler("email_sync")
def _handle_email_sync(payload):
    from modules.email.gmail import sync_emails
    query = (payload or {}).get("query", "newer_than:2d")
    return sync_emails(query=query)


@register_handler("sms_process")
def _handle_sms_process(payload):
    from routes.sms import process_pending_sms
    return process_pending_sms()


@register_handler("pdf_process")
def _handle_pdf_process(payload):
    """Process unparsed PDF attachments. Uses process_unparsed for efficiency —
    only processes PDFs that haven't been parsed yet, not all PDFs on disk."""
    from routes.pdf import process_unparsed_pdfs
    return process_unparsed_pdfs()


@register_handler("cross_match")
def _handle_cross_match(payload):
    from modules.cross_matcher import run_cross_match
    return run_cross_match()


@register_handler("enrichment")
def _handle_enrichment(payload):
    from modules.email.enrichment import run_enrichment
    return run_enrichment()


@register_handler("relink_enrichments")
def _handle_relink_enrichments(payload):
    """Post-enrichment fix-up: link orphan enrichments + upgrade bare merchants."""
    from modules.email.relink import run_relink
    return run_relink()


@register_handler("upi_classify")
def _handle_upi_classify(payload):
    """Seed upi_mappings from resolved transactions, then apply them (iter-16 C9)."""
    from modules.upi import run_upi_classification
    return run_upi_classification()


@register_handler("cards_maintenance")
def _handle_cards_maintenance(payload):
    """iter-16 C1/C2/C4-5: capture authoritative statement totals from SMS, reconcile them
    against parsed sums, attribute card-less SBI BBPS payments, and backfill card_id FKs.
    Order matters: statements before reconcile and before BBPS attribution (both read
    stated_total).

    iter-17 P1/P2: statement-row and payment backfills used to run only at container
    startup, so a long-running container recorded no new payments or statement rows
    (payments froze at 2026-09-04 during a 10-day uptime). Both are idempotent, so they
    now run here every hour, ahead of the stated-total and reconcile passes."""
    from services.cards_service import (
        backfill_statements_logic, backfill_payments_logic,
        statements_from_sms_logic, reconcile_statements_logic,
        attribute_bbps_payments_logic, backfill_card_ids_logic,
    )
    return {
        "statements": backfill_statements_logic(),
        "payments": backfill_payments_logic(),
        "statements_from_sms": statements_from_sms_logic(),
        "reconcile": reconcile_statements_logic(),
        "bbps_attribution": attribute_bbps_payments_logic(),
        "card_ids": backfill_card_ids_logic(),
    }


@register_handler("recategorize")
def _handle_recategorize(payload):
    """Re-run the categoriser over history (iter-18 M14). Writes only when the payload says
    {"dry_run": false}; anything else is a dry run that reports the transitions."""
    from routes.transactions import recategorize_all
    return recategorize_all(dry_run=(payload or {}).get("dry_run", True) is not False)


@register_handler("bank_alert_emails")
def _handle_bank_alert_emails(payload):
    """iter-18 H4: bank transaction-alert emails become transactions (linked to any SMS /
    statement copy of the same purchase on insert). Runs after email_sync."""
    from modules.email.bank_alerts import process_bank_alert_emails
    return process_bank_alert_emails(get_db())


@register_handler("daily_digest")
def _handle_daily_digest(payload):
    """iter-18 HP6: daily correctness digest (quarantine, dues with bank dates, reviews)."""
    from modules.digest import send_daily_digest
    return send_daily_digest(get_db())


@register_handler("food_process")
def _handle_food_process(payload):
    """Parse Swiggy/Toing food-invoice PDFs into food_orders."""
    from modules.food.service import process_food_orders
    only_new = not (payload or {}).get("backfill", False)
    return process_food_orders(only_new=only_new)


# --- Task runner ---

def _run_one_task():
    """Pick up and execute one pending task. Returns True if a task was found."""
    conn = get_db()
    row = conn.execute(
        "SELECT id, task_type, payload, retry_count FROM task_queue "
        "WHERE status = 'pending' ORDER BY id ASC LIMIT 1"
    ).fetchone()

    if not row:
        return False

    task_id = row["id"]
    task_type = row["task_type"]
    payload = None
    if row["payload"]:
        try:
            payload = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            pass

    # Mark as running
    conn.execute(
        "UPDATE task_queue SET status = 'running', started_at = datetime('now') WHERE id = ?",
        (task_id,)
    )
    conn.commit()

    handler = TASK_HANDLERS.get(task_type)
    if not handler:
        conn.execute(
            "UPDATE task_queue SET status = 'failed', error = ?, completed_at = datetime('now') WHERE id = ?",
            (f"Unknown task type: {task_type}", task_id)
        )
        conn.commit()
        return True

    try:
        result = handler(payload)
        result_json = json.dumps(result) if result else None
        conn.execute(
            "UPDATE task_queue SET status = 'completed', result = ?, completed_at = datetime('now') WHERE id = ?",
            (result_json, task_id)
        )
        conn.commit()
        print(f"[QUEUE] Task {task_id} ({task_type}) completed")
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        retry_count = row["retry_count"] + 1
        if retry_count < MAX_RETRIES:
            conn.execute(
                "UPDATE task_queue SET status = 'pending', error = ?, retry_count = ? WHERE id = ?",
                (error_msg, retry_count, task_id)
            )
            print(f"[QUEUE] Task {task_id} ({task_type}) failed, retrying ({retry_count}/{MAX_RETRIES})")
        else:
            conn.execute(
                "UPDATE task_queue SET status = 'failed', error = ?, retry_count = ?, completed_at = datetime('now') WHERE id = ?",
                (error_msg, retry_count, task_id)
            )
            print(f"[QUEUE] Task {task_id} ({task_type}) failed permanently: {error_msg}")
        conn.commit()

    return True


_runner_thread = None
_stop_event = threading.Event()


def recover_stuck_tasks() -> dict:
    """Recover tasks orphaned in 'running' (e.g. the process restarted mid-task).
    The runner only ever selects status='pending', so an orphaned row is otherwise
    never re-picked and its work is lost forever — exactly what stranded the
    enrichment task on 2026-04-08. Tasks already at MAX_RETRIES are failed rather
    than re-queued, so a process-crashing task can't loop on every restart."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, retry_count FROM task_queue WHERE status = 'running'"
    ).fetchall()
    recovered = failed = 0
    for r in rows:
        if (r["retry_count"] or 0) >= MAX_RETRIES:
            conn.execute(
                "UPDATE task_queue SET status='failed', "
                "error=COALESCE(error,'')||' [orphaned running; max retries on restart]', "
                "completed_at=datetime('now') WHERE id=?",
                (r["id"],),
            )
            failed += 1
        else:
            conn.execute(
                "UPDATE task_queue SET status='pending', retry_count=retry_count+1, "
                "error=COALESCE(error,'')||' [recovered orphaned running on restart]' WHERE id=?",
                (r["id"],),
            )
            recovered += 1
    conn.commit()
    if recovered or failed:
        print(f"[QUEUE] Stuck-task recovery: {recovered} re-queued, {failed} failed (max retries)")
    return {"recovered": recovered, "failed": failed}


def sweep_stuck_running() -> int:
    """Fail any task hung in 'running' past STUCK_TIMEOUT_MINUTES (lost/hung worker).
    Runs periodically while the queue is idle. Returns the number swept."""
    conn = get_db()
    cur = conn.execute(
        "UPDATE task_queue SET status='failed', "
        f"error=COALESCE(error,'')||' [timed out after {STUCK_TIMEOUT_MINUTES} min]', "
        "completed_at=datetime('now') "
        "WHERE status='running' AND started_at IS NOT NULL "
        "AND started_at < datetime('now', ?)",
        (f"-{STUCK_TIMEOUT_MINUTES} minutes",),
    )
    conn.commit()
    if cur.rowcount:
        print(f"[QUEUE] Swept {cur.rowcount} stuck 'running' task(s) past timeout")
    return cur.rowcount


def start_runner():
    """Start the background task runner thread."""
    global _runner_thread
    if _runner_thread and _runner_thread.is_alive():
        return

    _stop_event.clear()
    # Recover anything orphaned by a previous restart before we start polling.
    recover_stuck_tasks()

    def _loop():
        global _consecutive_failures, _last_error, _last_sweep
        while not _stop_event.is_set():
            try:
                found = _run_one_task()
                _consecutive_failures = 0
                _last_error = None
                if not found:
                    # Throttled: this issues a write, and running it every 5s forever
                    # was itself a source of lock contention.
                    now = time.time()
                    if now - _last_sweep >= SWEEP_INTERVAL:
                        _last_sweep = now
                        sweep_stuck_running()
                    _stop_event.wait(POLL_INTERVAL)
            except Exception as e:
                # Roll back before retrying. Without this a failed write leaves the
                # connection in a broken transaction state and every later statement
                # on this thread fails too.
                try:
                    get_db().execute("ROLLBACK")
                except Exception:
                    pass

                _consecutive_failures += 1
                _last_error = f"{type(e).__name__}: {e}"

                backoff = min(POLL_INTERVAL * (2 ** (_consecutive_failures - 1)), MAX_BACKOFF)
                # Log the first few in full, then only on escalation — the point is to
                # stay readable, not to write 100k identical lines.
                if _consecutive_failures <= 3:
                    print(f"[QUEUE] Runner error ({_consecutive_failures}): {_last_error}")
                    traceback.print_exc()
                elif _consecutive_failures == UNHEALTHY_AFTER:
                    print(
                        f"[QUEUE] UNHEALTHY — {_consecutive_failures} consecutive failures, "
                        f"backing off {backoff}s. Last error: {_last_error}"
                    )
                elif _consecutive_failures % 50 == 0:
                    print(
                        f"[QUEUE] still failing after {_consecutive_failures} attempts: {_last_error}"
                    )
                _stop_event.wait(backoff)

    _runner_thread = threading.Thread(target=_loop, daemon=True, name="task-runner")
    _runner_thread.start()
    print("[QUEUE] Task runner started")


def stop_runner():
    """Stop the background task runner."""
    _stop_event.set()
