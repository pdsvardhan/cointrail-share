"""iter-18 HP6: owner notifications (modules/notify.py). Nothing here touches the network:
urllib's urlopen is replaced with a recorder."""
import json

import pytest

from modules import notify

TOKEN = "123456:TEST-token-not-real"
CHANNEL_VARS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN_FILE", "TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID_FILE",
                "NTFY_URL", "NTFY_TOKEN", "NTFY_TOKEN_FILE", "INGEST_ALERT_WEBHOOK")


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def sent(monkeypatch):
    for var in CHANNEL_VARS:
        monkeypatch.delenv(var, raising=False)
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append({"url": req.full_url, "body": req.data.decode("utf-8"),
                      "headers": {k.lower(): v for k, v in req.header_items()}, "timeout": timeout})
        return _Resp()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_nothing_configured_logs_and_returns_false(sent, capsys):
    assert notify.send("Cointrail digest", "body") is False
    assert sent == []
    assert "no channel configured" in capsys.readouterr().out
    assert notify.main(["Title", "text"]) == 2


def test_telegram_send_message(sent, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1111, 2222")
    assert notify.send("Cointrail digest", "Card dues\n• HDFC ••4956 due 21 Oct") is True
    assert [c["url"] for c in sent] == [f"https://api.telegram.org/bot{TOKEN}/sendMessage"] * 2
    bodies = [json.loads(c["body"]) for c in sent]
    assert [b["chat_id"] for b in bodies] == ["1111", "2222"]
    assert bodies[0]["text"] == "Cointrail digest\n\nCard dues\n• HDFC ••4956 due 21 Oct"
    assert sent[0]["timeout"] == notify.TIMEOUT_S


def test_secrets_can_come_from_mounted_files(sent, monkeypatch, tmp_path):
    (tmp_path / "token.txt").write_text(TOKEN + "\n")
    (tmp_path / "chats.txt").write_text("3333\n")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_FILE", str(tmp_path / "token.txt"))
    monkeypatch.setenv("TELEGRAM_CHAT_ID_FILE", str(tmp_path / "chats.txt"))
    assert notify.configured_channels() == ["telegram"]
    assert notify.send("t", "x") is True
    assert sent[0]["url"].endswith(f"bot{TOKEN}/sendMessage") and json.loads(sent[0]["body"])["chat_id"] == "3333"


def test_long_telegram_messages_are_split_under_the_limit(sent, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1111")
    text = "\n".join(f"• line {i} " + "x" * 80 for i in range(120))
    assert notify.send("Digest", text) is True
    texts = [json.loads(c["body"])["text"] for c in sent]
    assert len(texts) > 1 and all(len(t) <= 4096 for t in texts)
    assert texts[0].startswith(f"(1/{len(texts)}) Digest")
    assert "".join(texts).count("• line") == 120                        # nothing dropped


def test_ntfy_and_webhook(sent, monkeypatch):
    monkeypatch.setenv("NTFY_URL", "https://ntfy.example.test/cointrail")
    monkeypatch.setenv("NTFY_TOKEN", "tk_test")
    monkeypatch.setenv("INGEST_ALERT_WEBHOOK", "https://hooks.example.test/x")
    assert notify.send("Cointrail digest 20 Oct", "Warnings\n• backup stale") is True
    ntfy, hook = sent
    assert ntfy["url"] == "https://ntfy.example.test/cointrail"
    assert ntfy["headers"]["title"] == "Cointrail digest 20 Oct"
    assert ntfy["headers"]["authorization"] == "Bearer tk_test"
    assert ntfy["body"] == "Warnings\n• backup stale"
    assert hook["url"] == "https://hooks.example.test/x"
    assert hook["body"] == "Cointrail digest 20 Oct\n\nWarnings\n• backup stale"


def test_full_card_and_account_numbers_never_leave(sent, monkeypatch):
    monkeypatch.setenv("INGEST_ALERT_WEBHOOK", "https://hooks.example.test/x")
    notify.send("Card 4321 5678 9012 3456", "A/c 000012345678901 debited; UPI ref 600000000013; "
                                            "card ••9320 due 2026-11-01, ₹1,08,767.00")
    body = sent[0]["body"]
    for full in ("4321 5678 9012 3456", "000012345678901", "600000000013"):
        assert full not in body
    assert "Card XX3456" in body and "A/c XX8901" in body and "ref XX0013" in body
    # last-4s, dates and amounts are left alone
    assert "••9320" in body and "2026-11-01" in body and "₹1,08,767.00" in body


def test_one_failing_channel_does_not_stop_the_others(sent, monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1111")
    monkeypatch.setenv("INGEST_ALERT_WEBHOOK", "https://hooks.example.test/x")
    recorder = notify.urllib.request.urlopen

    def flaky(req, timeout=None):
        if "telegram" in req.full_url:
            raise notify.urllib.error.URLError(f"connection refused for {req.full_url}")
        return recorder(req, timeout)

    monkeypatch.setattr(notify.urllib.request, "urlopen", flaky)
    assert notify.send("t", "x") is True
    out = capsys.readouterr().out
    assert "telegram failed" in out and TOKEN not in out              # the token is in the URL path
    assert notify.main(["t", "x"]) == 0


def test_every_channel_failing_returns_false(sent, monkeypatch):
    monkeypatch.setenv("INGEST_ALERT_WEBHOOK", "https://hooks.example.test/x")

    def down(req, timeout=None):
        raise OSError("network unreachable")

    monkeypatch.setattr(notify.urllib.request, "urlopen", down)
    assert notify.send("t", "x") is False
    assert notify.main(["t", "x"]) == 1
