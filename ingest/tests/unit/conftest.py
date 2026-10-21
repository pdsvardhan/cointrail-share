"""
Unit-test fixtures. Every test gets a brand-new SQLite file with the full production schema;
nothing here can reach the live database (the fixture refuses /data/ingest.db outright).
"""
import atexit
import os
import shutil
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_BOOT = tempfile.mkdtemp(prefix="ingest-test-")
atexit.register(shutil.rmtree, _BOOT, ignore_errors=True)
os.environ["INGEST_DB"] = os.path.join(_BOOT, "bootstrap.db")
os.environ["CARDS_CONFIG_PATH"] = os.path.join(_BOOT, "cards.json")

import config  # noqa: E402
import db  # noqa: E402

LIVE_DB = "/data/ingest.db"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    assert os.path.abspath(path) != LIVE_DB
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(config, "CARDS_CONFIG_PATH", str(tmp_path / "cards.json"))
    old = getattr(db._local, "conn", None)
    if old is not None:
        old.close()
    db._local.conn = None
    db.init_db()
    db.run_migrations()
    db.run_billing_migrations()
    db.run_c1_r4_migrations()
    db.run_iter18_migrations()
    db.run_iter203_be2_migrations()
    db.run_iter203_cards_migrations()
    from modules.categorizer import seed_aliases
    seed_aliases()
    c = db.get_db()
    assert c.execute("PRAGMA database_list").fetchone()["file"] == os.path.abspath(path)
    yield c
    c.close()
    db._local.conn = None


def add_card(conn, bank, last4, name="Card", statement_day=1, due_day=20, pdf_card_last4=None,
             active=1, network=None):
    conn.execute(
        "INSERT INTO cards (bank, last4, card_name, network, statement_day, due_day, pdf_card_last4, active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (bank, last4, name, network, statement_day, due_day, pdf_card_last4, active),
    )
    conn.commit()
    return conn.execute("SELECT id FROM cards WHERE bank=? AND last4=?", (bank, last4)).fetchone()["id"]


@pytest.fixture
def make_card(conn):
    def _make(*args, **kwargs):
        return add_card(conn, *args, **kwargs)
    return _make
