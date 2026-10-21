"""
Parse quarantine — iter-18 HP1.

Before iter-18, a bank SMS that no pattern could read was marked processed and forgotten. When
Axis reworded its spend alert on 2026-10-16, 6 of 7 Axis spends that month vanished and nothing
noticed. The rule now: a message that carries money (an amount AND a movement verb) is either
recorded as a transaction, recognised as known noise, recognised as a bill payment / statement,
or QUARANTINED here — never silently dropped.

The quarantine is grouped by template (digits masked) so one new bank wording shows up as one
line with a count, and is surfaced by /health and the daily digest. When a pattern is added,
retrying the quarantine turns the held messages into transactions.
"""
import re

MONEY_RE = re.compile(r"(?:\brs\.?|\binr|₹|\busd|\beur|\bgbp|\baed|\bsgd)\s*\.?\s*\d[\d,]*", re.I)
VERB_RE = re.compile(
    r"debited|spent|credited|refunded|revers|withdrawn|\bpaid\b|\bsent\b|purchase|\btxn\b|"
    r"\btrxn\b|transaction|deducted|cashback|cash back", re.I)

BANK_HINTS = (
    ("HDFC", r"hdfc"), ("SBI", r"\bsbi|sbicrd|cbssbi|sbiupi|sbiinb|sbi card"), ("ICICI", r"icici"),
    ("Axis", r"axis"), ("HSBC", r"hsbc"), ("Kotak", r"kotak"), ("IndusInd", r"indusind"),
    ("Yes", r"yesbnk|yes bank"), ("CRED", r"\bcred\b"),
)


def looks_like_money_movement(body):
    """True if the text names an amount AND a verb that moves money."""
    b = body or ""
    return bool(MONEY_RE.search(b) and VERB_RE.search(b))


def bank_hint(sender, body):
    s = f"{sender or ''} {body or ''}".lower()
    for name, pat in BANK_HINTS:
        if re.search(pat, s):
            return name
    return None


def template_of(body):
    """Digits masked, whitespace collapsed — one line per bank wording."""
    t = re.sub(r"\s+", " ", body or "")
    t = re.sub(r"\d[\d,./:-]*", "#", t)
    return t[:120]


def quarantine(conn, source, source_ref, sender, body, reason):
    """Hold a message no rule could turn into a transaction. Idempotent per (source, source_ref):
    a repeat bumps `attempts` and `last_tried` instead of adding a row."""
    conn.execute(
        "INSERT INTO parse_quarantine (source, source_ref, sender, bank, reason, template, excerpt) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(source, source_ref) DO UPDATE SET "
        "attempts = attempts + 1, last_tried = datetime('now'), reason = excluded.reason, "
        "resolved_at = NULL, resolution = NULL",
        (source, str(source_ref), (sender or "")[:60], bank_hint(sender, body), reason,
         template_of(body), (body or "")[:300]),
    )


def resolve(conn, source, source_ref, resolution, transaction_id=None):
    conn.execute(
        "UPDATE parse_quarantine SET resolved_at = datetime('now'), resolution = ?, transaction_id = ? "
        "WHERE source = ? AND source_ref = ? AND resolved_at IS NULL",
        (resolution, transaction_id, source, str(source_ref)),
    )


def summary(conn, limit=25, source=None):
    """Open quarantine grouped by bank + template, newest first; `source` ('sms'/'email') narrows it."""
    where, args = "resolved_at IS NULL", []
    if source:
        where += " AND source = ?"
        args.append(source)
    total = conn.execute(f"SELECT COUNT(*) c FROM parse_quarantine WHERE {where}", args).fetchone()["c"]
    groups = conn.execute(
        "SELECT bank, template, COUNT(*) n, MAX(first_seen) newest, MIN(excerpt) example "
        f"FROM parse_quarantine WHERE {where} "
        "GROUP BY bank, template ORDER BY newest DESC LIMIT ?",
        (*args, limit),
    ).fetchall()
    by_bank = conn.execute(
        "SELECT COALESCE(bank, 'unknown') bank, COUNT(*) n FROM parse_quarantine "
        f"WHERE {where} GROUP BY 1 ORDER BY 2 DESC", args
    ).fetchall()
    return {
        "open": total,
        "by_bank": {r["bank"]: r["n"] for r in by_bank},
        "templates": [dict(r) for r in groups],
    }
