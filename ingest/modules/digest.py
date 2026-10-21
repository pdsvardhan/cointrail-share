"""
Daily digest — iter-18 HP6.

Everything the audit found went wrong silently. The digest is the one message a day that says
what needs the owner: card dues (with the BANK's due date), bank wordings no rule could read
(the quarantine), statements whose printed total and parsed lines disagree, and the /health
correctness warnings. It is built from the same sources the app shows — /cards/summary
(contract 3) for dues, modules.quarantine for the quarantine, routes.system.health_report for
warnings — so the digest can never tell a different story than the screen.

Cards appear as bank + product + last 4 digits only; modules.notify masks anything longer.
"""
from datetime import date, timedelta

from util.timez import today_ist

QUARANTINE_LINES = 8
TEMPLATE_CHARS = 90
REVIEW_LINES = 8
OWED_STATUSES = ("overdue", "unpaid", "partial")


def _inr(amount):
    """Indian digit grouping: 108767 -> ₹1,08,767.00."""
    if amount is None:
        return "amount unknown"
    whole, frac = f"{abs(float(amount)):.2f}".split(".")
    if len(whole) > 3:
        head, groups = whole[:-3], [whole[-3:]]
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups)
    return f"{'-' if float(amount) < 0 else ''}₹{whole}.{frac}"


def _card_label(card):
    last4 = str(card.get("last4") or "")[-4:]
    name = " ".join(p for p in (card.get("bank"), card.get("card_name")) if p)
    return f"{name} ••{last4}" if last4 else name


def _day(iso, today):
    d = date.fromisoformat(iso[:10])
    return f"{d.day} {d:%b}" if d.year == today.year else f"{d.day} {d:%b %Y}"


def _when(iso, today):
    n = (date.fromisoformat(iso[:10]) - today).days
    if n == 0:
        return "today"
    if n == 1:
        return "tomorrow"
    if n == -1:
        return "yesterday"
    return f"in {n} days" if n > 0 else f"{-n} days ago"


def _dues_section(today):
    """Owed / overdue / unconfirmed cards from /cards/summary (contract 3, computed in IST)."""
    from routes import cards as cards_routes
    try:
        cards = cards_routes.cards_summary()["cards"]
    except Exception as e:
        return [f"• Card dues unavailable: {type(e).__name__}: {e}"]

    if any("payment_status" not in c for c in cards):
        return ["• Card dues unavailable: /cards/summary has no payment_status "
                "(cards contract 3 is not deployed)."]

    owed, unconfirmed = [], []
    for card in cards:
        status = card["payment_status"]
        stmt = card.get("latest_statement") or {}
        due = stmt.get("due_date")
        source = "bank date" if stmt.get("due_date_source") == "bank" else "estimated"
        if status in OWED_STATUSES:
            amount = card.get("remaining_due")
            if amount is None:
                amount = stmt.get("amount_due")
            if not due:
                when = f"due date unknown ({source})"
            elif status == "overdue":
                when = f"was due {_day(due, today)} ({_when(due, today)}, {source})"
            else:
                when = f"due {_day(due, today)} ({_when(due, today)}, {source})"
            prefix = "OVERDUE " if status == "overdue" else ""
            part = ", part paid" if status == "partial" else ""
            owed.append((status != "overdue", due or "9999-12-31",
                         f"• {prefix}{_card_label(card)}: {_inr(amount)} {when}{part}"))
        elif status == "not_confirmed":
            closed = stmt.get("billing_end")
            closed_txt = f"closed {_day(closed, today)}" if closed else "latest"
            unconfirmed.append(
                f"• {_card_label(card)}: no payment seen for the statement {closed_txt} "
                f"({_inr(stmt.get('amount_due'))}) — check the bank app")
    lines = [line for _o, _d, line in sorted(owed)] + unconfirmed
    return lines or ["• Nothing owed right now."]


def _quarantine_section(conn):
    from modules.quarantine import summary
    q = summary(conn, limit=QUARANTINE_LINES)
    if not q["open"]:
        return None
    lines = [f"Quarantine — {q['open']} bank message(s) no rule could read"]
    for t in q["templates"]:
        template = (t["template"] or "").strip()
        if len(template) > TEMPLATE_CHARS:
            template = template[:TEMPLATE_CHARS - 1] + "…"
        lines.append(f"• {t['bank'] or 'unknown'} ×{t['n']}: {template}")
    shown = sum(t["n"] for t in q["templates"])
    if shown < q["open"]:
        lines.append(f"• …and {q['open'] - shown} more")
    return lines


def _review_section(conn, today):
    # Same window as the /health statements_needs_review warning, so the two always agree.
    from routes.system import REVIEW_RECENT_DAYS
    cutoff = (today - timedelta(days=REVIEW_RECENT_DAYS)).isoformat()
    rows = conn.execute(
        "SELECT c.bank, c.card_name, c.last4, s.billing_end, s.stated_total, s.computed_total "
        "FROM statements s JOIN cards c ON c.id = s.card_id "
        "WHERE s.needs_review = 1 AND s.billing_end >= ? ORDER BY s.billing_end DESC",
        (cutoff,),
    ).fetchall()
    if not rows:
        return None
    lines = [f"Statements to review — bank total and parsed lines disagree ({len(rows)})"]
    for r in rows[:REVIEW_LINES]:
        lines.append(
            f"• {_card_label(dict(r))} closed {_day(r['billing_end'], today)}: "
            f"bank {_inr(r['stated_total'])} vs parsed {_inr(r['computed_total'])}")
    if len(rows) > REVIEW_LINES:
        lines.append(f"• …and {len(rows) - REVIEW_LINES} more")
    return lines


def _cancelled_charged_section(conn, today):
    """iter-203 E5: subscriptions the owner cancelled that charged again."""
    from services.recurring_service import cancelled_series_charged_again
    again = cancelled_series_charged_again(conn)
    if not again:
        return None
    lines = [f"Cancelled but charged again ({len(again)})"]
    for s in again:
        for ch in s["charges"]:
            lines.append(f"• {s['merchant']}: {_inr(ch['amount'])} on {_day(ch['date'], today)}, "
                         f"after you cancelled it on {_day(s['cancelled_on'], today)}")
    return lines


def _silent_section(conn, today):
    """Accounts that went quiet for longer than their usual gap, by name (iteration 203 E13); the
    public /health warning only counts them by bank."""
    from routes.system import silent_accounts
    rows = [r for r in silent_accounts(conn, today) if r["state"] == "silent"]
    if not rows:
        return None
    lines = [f"Silent accounts — quiet for longer than usual ({len(rows)})"]
    for r in sorted(rows, key=lambda r: -r["silent_days"]):
        lines.append(f"• {_card_label({'bank': r['bank'], 'card_name': r['name'], 'last4': r['last4']})}: "
                     f"last transaction {_day(r['last_seen'], today)} ({r['silent_days']} days; usually every "
                     f"{r['usual_gap_days']} days or less)")
    return lines


def build_digest(conn):
    """The digest as plain text (Telegram/ntfy/webhook all take text)."""
    from routes.system import health_report
    today = today_ist()
    health = health_report(conn)

    sections = [[f"Cointrail daily digest · {today:%a %d %b %Y}"]]
    if health["status"] != "ok":
        sections.append([f"Health: {health['status']}"] + [f"• {i}" for i in health["issues"]])
    sections.append(["Card dues"] + _dues_section(today))
    if health["warnings"]:
        sections.append(["Warnings"] + [f"• {w}" for w in health["warnings"]])
    for extra in (_quarantine_section(conn), _review_section(conn, today),
                  _cancelled_charged_section(conn, today), _silent_section(conn, today)):
        if extra:
            sections.append(extra)
    if health["status"] == "ok" and not health["warnings"]:
        # Only what the checks actually established: on an empty DB there is no "SMS flowing".
        sections.append(["All checks clean: health ok and no correctness warnings."])
    return "\n\n".join("\n".join(s) for s in sections)


def send_daily_digest(conn=None, include_text=False):
    """Build + send. Registered as the `daily_digest` queue task (09:00 IST) and POST /digest/send.
    The task result is stored in task_queue, so the text is only returned when asked for."""
    from db import get_db
    from modules import notify
    conn = conn or get_db()
    text = build_digest(conn)
    sent = notify.send(f"Cointrail digest {today_ist():%d %b}", text)
    result = {"sent": sent, "channels": notify.configured_channels(), "chars": len(text)}
    if include_text:
        result["text"] = text
    return result
