"""iter-18 CH2: the upgraded dependency set still does what Ingest needs from it.

These run inside the python:3.12 test image (scripts/run-tests.sh) where requirements.txt is
installed; on a workstation without the PDF/scheduler libraries they are skipped, not failed."""
from datetime import datetime, timezone

import pytest

from util.timez import IST


def _encrypted_statement(path, password):
    pikepdf = pytest.importorskip("pikepdf")
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(400, 200))
    font = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica))
    page.obj[pikepdf.Name.Resources] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    page.obj[pikepdf.Name.Contents] = pdf.make_stream(
        b"BT /F1 12 Tf 20 100 Td (Total Amount Due 1051.38 Due on 01-11-26) Tj ET")
    pdf.save(path, encryption=pikepdf.Encryption(owner="owner-pass", user=password, R=6))


def test_encrypted_statement_decrypts_and_parses(tmp_path, monkeypatch):
    pdfplumber = pytest.importorskip("pdfplumber")
    from modules.pdf.decrypt import decrypt_pdf, is_encrypted
    path = str(tmp_path / "statement.pdf")
    _encrypted_statement(path, "right-pass")
    monkeypatch.setenv("PDF_PASSWORD_AXIS", "wrong-pass, right-pass")
    assert is_encrypted(path) is True
    out = decrypt_pdf(path, bank_hint="axis")
    assert out and out.endswith(".decrypted.pdf")
    with pdfplumber.open(out) as pdf:
        assert "Total Amount Due 1051.38" in pdf.pages[0].extract_text()


def test_apscheduler_3x_fires_the_digest_at_9_ist():
    pytest.importorskip("apscheduler")
    import apscheduler
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    assert apscheduler.__version__.startswith("3.")
    trigger = CronTrigger(hour=9, minute=0, timezone=IST)
    # 10:30 IST on 20 Oct -> the next run is 09:00 IST on 21 Oct, whatever TZ the container has.
    nxt = trigger.get_next_fire_time(None, datetime(2026, 10, 20, 5, 0, tzinfo=timezone.utc))
    assert nxt.astimezone(IST).isoformat() == "2026-10-21T09:00:00+05:30"
    sched = BackgroundScheduler()
    sched.add_job(lambda: None, "interval", hours=1, id="hourly_sync")       # main.py's 3.x call shape
    assert sched.get_job("hourly_sync") is not None


def test_multipart_upload_still_parses():
    pytest.importorskip("python_multipart")
    from fastapi import FastAPI, File, UploadFile
    from fastapi.testclient import TestClient
    app = FastAPI()

    @app.post("/upload")
    async def upload(file: UploadFile = File(...)):
        return {"name": file.filename, "size": len(await file.read())}

    r = TestClient(app).post("/upload", files={"file": ("s.pdf", b"%PDF-1.7 test", "application/pdf")})
    assert r.status_code == 200 and r.json() == {"name": "s.pdf", "size": 13}


def test_main_imports_with_every_route(monkeypatch):
    for lib in ("apscheduler", "pikepdf", "pdfplumber", "python_multipart", "googleapiclient"):
        pytest.importorskip(lib)
    monkeypatch.setenv("INGEST_API_KEY", "test-key-not-a-real-secret")
    import main
    # fastapi >= 0.14x keeps included routers nested in app.routes (_IncludedRouter), so the
    # route table is read from the OpenAPI schema, which is what clients actually see.
    paths = set(main.app.openapi()["paths"])
    assert {"/health", "/digest", "/digest/send", "/sms", "/sms/import/xml", "/sms/process-pending",
            "/cross-match", "/cards/summary", "/stats", "/cron/trigger"} <= paths
    assert len(paths) >= 60                                     # every router is mounted (64 today)
