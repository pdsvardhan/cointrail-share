"""
Owner notifications — iter-18 HP6 / H7.

Until iter-18 every problem the system could detect (a wedged queue, a dead SMS backup, a bank
wording no rule reads) went into a log file nobody reads. This module is the one way Ingest and
the host scripts (ops/) talk to the owner: the daily digest, health warnings, a failed backup pull.

Channels are configured by environment (see config.get_notify_settings); every configured channel
gets the message. None configured -> the message is logged and send() returns False, so a caller
can tell "told the owner" apart from "nobody heard".

Privacy rule: no full card or account number ever leaves the box. Callers already print cards as
last-4, and _redact() masks any long digit run as a last line of defence, because these messages
go to third-party services (Telegram, ntfy).

CLI (used by the host scripts so they share this exact channel config):
    docker exec ingest python -m modules.notify "Title" "Text"     (or text on stdin)
exit 0 = delivered to at least one channel, 1 = every channel failed, 2 = no channel configured.
"""
import json
import re
import sys
import urllib.error
import urllib.request

import config

TIMEOUT_S = 10
# Telegram rejects messages over 4096 characters; leave room for the "(2/3)" chunk prefix.
TELEGRAM_CHUNK = 3900

# Card numbers printed in 4-digit groups ("4321 5678 9012 3456"), and any unbroken run of 9+
# digits (account numbers are 9-18 digits, phone numbers 10, UPI refs 12). Only the last 4
# survive. Dates (2026-10-20), amounts (13,836.54) and last-4s are never this long.
_GROUPED_CARD_RE = re.compile(r"\b\d{4}(?:[ -]\d{4}){2,3}\b")
_LONG_DIGITS_RE = re.compile(r"\d{9,}")


def _mask(match):
    digits = re.sub(r"\D", "", match.group(0))
    return "XX" + digits[-4:]


def _redact(text):
    text = _GROUPED_CARD_RE.sub(_mask, text or "")
    return _LONG_DIGITS_RE.sub(_mask, text)


def configured_channels(settings=None):
    s = settings or config.get_notify_settings()
    channels = []
    if s["telegram_bot_token"] and s["telegram_chat_ids"]:
        channels.append("telegram")
    if s["ntfy_url"]:
        channels.append("ntfy")
    if s["webhook_url"]:
        channels.append("webhook")
    return channels


def _post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return 200 <= resp.status < 300


def _chunks(text, size):
    if len(text) <= size:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        while len(line) > size:          # a single very long line: hard-split it
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:size])
            line = line[size:]
        if current and len(current) + 1 + len(line) > size:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    total = len(parts)
    return [f"({i}/{total}) {p}" for i, p in enumerate(parts, 1)]


def _send_telegram(s, message):
    url = f"https://api.telegram.org/bot{s['telegram_bot_token']}/sendMessage"
    ok = True
    for chat_id in s["telegram_chat_ids"]:
        for chunk in _chunks(message, TELEGRAM_CHUNK):
            body = json.dumps({"chat_id": chat_id, "text": chunk,
                               "disable_web_page_preview": True}).encode("utf-8")
            ok = _post(url, body, {"Content-Type": "application/json"}) and ok
    return ok


def _header_safe(value):
    # urllib sends headers as latin-1; ntfy titles are short labels, so fold anything else.
    return value.encode("latin-1", "replace").decode("latin-1")


def _send_ntfy(s, title, text):
    headers = {"Title": _header_safe(title), "Content-Type": "text/plain; charset=utf-8"}
    if s["ntfy_token"]:
        headers["Authorization"] = f"Bearer {s['ntfy_token']}"
    return _post(s["ntfy_url"], text.encode("utf-8"), headers)


def _send_webhook(s, message):
    return _post(s["webhook_url"], message.encode("utf-8"),
                 {"Content-Type": "text/plain; charset=utf-8"})


def _scrub(error, settings):
    """An error message must never carry a secret: the Telegram token sits in the URL path."""
    msg = f"{type(error).__name__}: {error}"
    for secret in (settings["telegram_bot_token"], settings["ntfy_token"]):
        if secret:
            msg = msg.replace(secret, "***")
    return msg


def send(title, text):
    """Deliver title + text to every configured channel. True if at least one accepted it."""
    s = config.get_notify_settings()
    title = _redact(title).strip()
    text = _redact(text).strip()
    channels = configured_channels(s)
    if not channels:
        print(f"[NOTIFY] no channel configured; not sent: {title}")
        return False
    message = f"{title}\n\n{text}" if text else title
    delivered = []
    for channel in channels:
        try:
            if channel == "telegram":
                ok = _send_telegram(s, message)
            elif channel == "ntfy":
                ok = _send_ntfy(s, title, text or title)
            else:
                ok = _send_webhook(s, message)
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"[NOTIFY] {channel} failed: {_scrub(e, s)}")
            ok = False
        if ok:
            delivered.append(channel)
    print(f"[NOTIFY] '{title}' -> delivered via {delivered or 'nothing'} (configured {channels})")
    return bool(delivered)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: python -m modules.notify TITLE [TEXT]  (TEXT may come on stdin)")
        return 2
    title = args[0]
    text = args[1] if len(args) > 1 else ("" if sys.stdin.isatty() else sys.stdin.read())
    if not configured_channels():
        print(f"[NOTIFY] no channel configured; not sent: {title}")
        return 2
    return 0 if send(title, text) else 1


if __name__ == "__main__":
    sys.exit(main())
