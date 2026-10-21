"""
Ingest — Personal data ingestion service.
Collects SMS, email, PDF data. Classifies, categorizes, serves via API.
"""
import os

from fastapi import FastAPI

from middleware.security import setup_cors, setup_auth
from db import (
    init_db, run_migrations, run_billing_migrations, run_c1_r4_migrations,
    run_iter18_migrations, run_iter203_cards_migrations, sync_cards_config, get_db,
)
from db import run_iter203_be2_migrations
from modules.categorizer import seed_aliases
from services.queue import init_queue_table, enqueue, start_runner


app = FastAPI(title="Ingest", description="Personal data ingestion service")

# --- Security ---
setup_cors(app)
setup_auth(app)

# --- Routes ---
from routes.sms import router as sms_router
from routes.email import router as email_router
from routes.pdf import router as pdf_router
from routes.transactions import router as txn_router
from routes.cards import router as cards_router
from routes.system import router as sys_router
from routes.tasks import router as tasks_router
from routes.content import router as content_router
from routes.analytics import router as analytics_router
from routes.food import router as food_router

app.include_router(sms_router, prefix="/sms", tags=["SMS"])
app.include_router(email_router, prefix="/email", tags=["Email"])
app.include_router(pdf_router, prefix="/pdf", tags=["PDF"])
app.include_router(txn_router, tags=["Transactions"])
app.include_router(cards_router, prefix="/cards", tags=["Cards"])
app.include_router(sys_router, tags=["System"])
app.include_router(tasks_router, prefix="/tasks", tags=["Tasks"])
app.include_router(content_router, prefix="/content", tags=["Content"])
app.include_router(analytics_router, tags=["Analytics"])
app.include_router(food_router, prefix="/food", tags=["Food"])


# --- Cron ---
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta


def _pending_count(task_type: str) -> int:
    """How many pending rows exist for a task type (used to report coalesce skips)."""
    from db import get_db
    row = get_db().execute(
        "SELECT COUNT(*) AS n FROM task_queue WHERE task_type = ? AND status = 'pending'",
        (task_type,),
    ).fetchone()
    return row["n"] if row else 0


def _hourly_sync():
    """Hourly job: enqueue sync tasks for background processing.

    Pipeline order matters:
    1. email_sync — fetch new emails, download PDF attachments
    2. pdf_process — parse all unparsed PDF attachments into transactions
    3. sms_process — process pending SMS into transactions
    4. cross_match — deduplicate SMS vs PDF transactions
    5. enrichment — enrich transaction metadata from email parsers
    6. relink_enrichments — link orphan enrichments to bare txns by date proximity

    These are enqueued with coalesce=True: if a previous hour's copy is still pending,
    we reuse it rather than stacking a duplicate. Every one of these tasks processes all
    outstanding work, so N pending copies do exactly as much as one — but they mask a
    stalled queue behind a growing backlog (see iter-16 / 2026-08-20).
    """
    print("[CRON] Enqueueing hourly sync tasks...")
    enqueued, skipped = 0, 0
    for task_type, payload in (
        ("email_sync", {"query": "newer_than:7d"}),
        ("bank_alert_emails", None),  # iter-18 H4 — after email_sync so new alerts are stored
        ("pdf_process", None),
        ("food_process", None),  # Swiggy/Toing invoice PDFs into food_invoice_orders
        ("sms_process", None),
        ("cross_match", None),
        ("enrichment", None),
        ("relink_enrichments", None),
        ("upi_classify", None),  # iter-16 C9 — map UPI VPAs to stable payee identities
        ("cards_maintenance", None),  # iter-16 C1/C2/C4-5 — statement totals, reconcile, BBPS, card_id
    ):
        before = _pending_count(task_type)
        enqueue(task_type, payload, coalesce=True)
        if _pending_count(task_type) > before:
            enqueued += 1
        else:
            skipped += 1
    print(f"[CRON] {enqueued} tasks enqueued, {skipped} skipped (already pending)")


HOURLY_TASKS = ["email_sync", "bank_alert_emails", "pdf_process", "food_process", "sms_process",
                "cross_match", "enrichment", "relink_enrichments", "upi_classify", "cards_maintenance"]

scheduler = BackgroundScheduler()
_next = datetime.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
scheduler.add_job(_hourly_sync, 'interval', hours=1, id='hourly_sync', next_run_time=_next)

# iter-18 HP6: one correctness digest a day at 09:00 IST (explicit timezone — the container is UTC).
from apscheduler.triggers.cron import CronTrigger
from util.timez import IST
scheduler.add_job(lambda: enqueue("daily_digest", None, coalesce=True),
                  CronTrigger(hour=9, minute=0, timezone=IST), id="daily_digest")


@app.post("/cron/trigger")
def trigger_cron():
    """Manually trigger the hourly sync (enqueues tasks)."""
    _hourly_sync()
    return {"status": "enqueued", "tasks": HOURLY_TASKS}


@app.get("/cron/status")
def cron_status():
    job = scheduler.get_job("hourly_sync")
    if job:
        return {"status": "active", "next_run": str(job.next_run_time)}
    return {"status": "not_found"}


# --- Startup ---
@app.on_event("startup")
def startup():
    init_db()
    run_migrations()
    run_billing_migrations()
    run_c1_r4_migrations()
    run_iter18_migrations()
    run_iter203_be2_migrations()
    run_iter203_cards_migrations()
    sync_cards_config()
    init_queue_table()
    count = seed_aliases()
    print(f"Seeded {count} merchant aliases")
    # Init source registry
    from sources.registry import init_sources
    init_sources()
    try:
        from services.cards_service import backfill_statements_logic, backfill_payments_logic
        result = backfill_statements_logic()
        print(f"Startup backfill: {result}")
        result2 = backfill_payments_logic()
        print(f"Startup payments: {result2}")
        # iter-16 C1/C2/C4-5: authoritative statement totals + reconciliation + BBPS
        # attribution + card_id backfill. Order: statements -> reconcile/attribute.
        from services.cards_service import (
            statements_from_sms_logic, reconcile_statements_logic,
            attribute_bbps_payments_logic, backfill_card_ids_logic,
        )
        print(f"Startup statements-from-sms: {statements_from_sms_logic()}")
        print(f"Startup reconcile: {reconcile_statements_logic()}")
        print(f"Startup bbps-attribution: {attribute_bbps_payments_logic()}")
        print(f"Startup card-id backfill: {backfill_card_ids_logic()}")
    except Exception as e:
        print(f"Startup backfill error (non-fatal): {e}")
    start_runner()
    scheduler.start()
    print("Task queue runner + hourly scheduler started")


if __name__ == "__main__":
    import uvicorn
    from config import HOST, PORT
    uvicorn.run(app, host=HOST, port=PORT)
