"""iter-18 CH3: API-key auth fails closed. Before iter-18 an unset INGEST_API_KEY served the whole
financial database to anyone (the middleware was simply not installed)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.security import setup_auth

KEY = "test-key-not-a-real-secret-0123456789"


def _client(monkeypatch, key=None, allow_no_auth=None):
    if key is None:
        monkeypatch.delenv("INGEST_API_KEY", raising=False)
    else:
        monkeypatch.setenv("INGEST_API_KEY", key)
    if allow_no_auth is None:
        monkeypatch.delenv("INGEST_ALLOW_NO_AUTH", raising=False)
    else:
        monkeypatch.setenv("INGEST_ALLOW_NO_AUTH", allow_no_auth)
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/stats")
    def stats():
        return {"transactions": 1}

    @app.post("/sms")
    def sms():
        return {"status": "stored"}

    setup_auth(app)
    return TestClient(app)


def test_unset_key_refuses_every_private_request_with_503(monkeypatch, capsys):
    c = _client(monkeypatch)
    assert "INGEST_API_KEY is not set" in capsys.readouterr().out      # logged loudly at startup
    r = c.get("/stats")
    assert r.status_code == 503
    assert "not set" in r.json()["detail"]
    assert c.post("/sms", json={}).status_code == 503
    # A key sent by the caller cannot unlock a server that has none configured.
    assert c.get("/stats", headers={"X-API-Key": "anything"}).status_code == 503
    assert "REFUSING GET /stats" in capsys.readouterr().out


def test_unset_key_keeps_health_public(monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_blank_key_counts_as_unset(monkeypatch):
    assert _client(monkeypatch, key="   ").get("/stats").status_code == 503


@pytest.mark.parametrize("flag", ["0", "true", "yes", ""])
def test_only_the_explicit_dev_flag_opens_the_api(monkeypatch, flag):
    assert _client(monkeypatch, allow_no_auth=flag).get("/stats").status_code == 503


def test_explicit_dev_flag_runs_without_auth(monkeypatch, capsys):
    c = _client(monkeypatch, allow_no_auth="1")
    assert "UNAUTHENTICATED" in capsys.readouterr().out
    assert c.get("/stats").status_code == 200


def test_wrong_or_missing_key_is_401(monkeypatch):
    c = _client(monkeypatch, key=KEY)
    assert c.get("/stats").status_code == 401
    assert c.get("/stats", headers={"X-API-Key": KEY + "x"}).status_code == 401


def test_right_key_is_200_and_health_stays_public(monkeypatch):
    c = _client(monkeypatch, key=KEY)
    r = c.get("/stats", headers={"X-API-Key": KEY})
    assert r.status_code == 200 and r.json() == {"transactions": 1}
    assert c.get("/health").status_code == 200


def test_cors_preflight_is_not_blocked(monkeypatch):
    c = _client(monkeypatch, key=KEY)
    assert c.options("/stats").status_code != 401
