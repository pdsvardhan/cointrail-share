"""
Credit card service: payment parsing, statement backfill, billing logic.
Extracted from main.py.

iter-18 (2026-10-20 audit) made the bank's own numbers the authority for a card's bill:
  - record_bank_statement / record_card_payment are the single write paths other workstreams use
    (statement PDFs, SMS, bank + CRED emails, CRED debits on the owner's bank accounts);
  - a statement keeps the bank's due date and minimum due (H1), the amount due is the stated total
    or the NET computed total, never gross debits (H2), and the paid/overdue verdict is computed
    in IST from payments since the statement closed (M11);
  - build_cards_summary is what /cards/summary serves.
"""
import calendar
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from itertools import combinations

import db as db_module
from db import get_db
from util.timez import ist_date_of, today_ist

# --- CC Payment SMS Patterns ---

CC_PAYMENT_PATTERNS = [
    re.compile(r"PAYMENT OF Rs\.?\s*([\d,]+\.?\d*)\s*RECEIVED TOWARDS.*CARD.*(?:ENDING\s*(?:WITH\s*)?|XX)(\d{4})", re.IGNORECASE),
    re.compile(r"Online Payment of Rs\.?\s*([\d,]+\.?\d*)\s*.*credited to your card ending\s*(\d{4})", re.IGNORECASE),
    re.compile(r"Payment of (?:Rs\.?|INR)\s*([\d,]+\.?\d*)\s*(?:has been )?received (?:on|for) your.*(?:Credit Card|Card).*?(?:XX|ending\s*(?:with\s*)?)(\d{4})", re.IGNORECASE),
    re.compile(r"Payment of Rs\.?\s*([\d,]+\.?\d*)\s*received towards.*Card.*ending\s*(\d{4})", re.IGNORECASE),
    re.compile(r"received a payment of (?:INR|Rs\.?)\s*([\d,]+\.?\d*)\s*for.*credit card.*?(?:ending|XX)\s*(?:with\s*)?(\d{4})", re.IGNORECASE),
    re.compile(r"Payment of INR\s*([\d,]+\.?\d*)\s*(?:has been )?received (?:towards|for|on) your.*(?:Credit Card|Card).*?(?:XX|ending\s*(?:with\s*)?)(\d{4})", re.IGNORECASE),
    re.compile(r"payment of INR\s*([\d,]+\.?\d*)\s*received towards.*HSBC.*Card", re.IGNORECASE),
    re.compile(r"Payment received.*Rs\.?\s*([\d,]+\.?\d*)\s*.*card.*ending\s*(\d{4})", re.IGNORECASE),
    re.compile(r"thank you for.*payment.*Rs\.?\s*([\d,]+\.?\d*)\s*.*card.*ending\s*(\d{4})", re.IGNORECASE),
    re.compile(r"[Pp]ayment.*?(?:Rs\.?|INR)\s*([\d,]+\.?\d*).*?(?:received|credited).*?(?:[Cc]ard).*?(?:XX|ending\s*(?:with\s*)?)(\d{4})", re.IGNORECASE),
]

SBI_BBPS_PATTERN = re.compile(
    r"received payment of Rs\.?\s*([\d,]+\.?\d*).*?BBPS.*?SBI Credit Card.*?available limit is Rs\.?\s*([\d,]+\.?\d*)",
    re.IGNORECASE | re.DOTALL
)

SBI_AUTO_DEBIT_PATTERN = re.compile(
    r"received payment of Rs\.?\s*([\d,]+\.?\d*).*?(?:Auto Debit|auto debit).*?SBI Card.*?(?:no\s*)?(?:xxxx|XXXX)?(\d{2,4})",
    re.IGNORECASE | re.DOTALL
)

# Map PDF card_last4 to real card_last4
PDF_CARD_ALIASES = {}


def load_pdf_aliases(conn=None):
    conn = conn or get_db()
    rows = conn.execute("SELECT last4, pdf_card_last4 FROM cards WHERE pdf_card_last4 IS NOT NULL").fetchall()
    PDF_CARD_ALIASES.clear()
    for r in rows:
        PDF_CARD_ALIASES[r["pdf_card_last4"]] = r["last4"]


def normalize_card_last4(last4):
    return PDF_CARD_ALIASES.get(last4, last4)


def parse_payment_sms(body):
    """Parse a CC payment SMS. Returns {amount, card_last4} or None."""
    sbi_m = SBI_BBPS_PATTERN.search(body)
    if sbi_m:
        # SBI's BBPS confirmation names no card. The old available-limit heuristic
        # (>50k -> 2971) broke once 2111's limit was raised (Oct-26 Rs46,088 for 2111 showed
        # avail Rs300k). Leave the card unset; attribute_bbps_payments_logic pins it by
        # matching the amount to a stated statement total. (iter-17 P4)
        amount = float(sbi_m.group(1).replace(",", ""))
        return {"amount": amount, "card_last4": None}

    sbi_ad = SBI_AUTO_DEBIT_PATTERN.search(body)
    if sbi_ad:
        amount = float(sbi_ad.group(1).replace(",", ""))
        card_suffix = sbi_ad.group(2)
        if card_suffix in ("71", "2971"):
            card_last4 = "2971"
        elif card_suffix in ("11", "2111"):
            card_last4 = "2111"
        else:
            card_last4 = card_suffix
        return {"amount": amount, "card_last4": card_last4}

    for pattern in CC_PAYMENT_PATTERNS:
        m = pattern.search(body)
        if m:
            amount = float(m.group(1).replace(",", ""))
            card_last4 = m.group(2) if m.lastindex >= 2 else None
            return {"amount": amount, "card_last4": card_last4}
    return None


# --- Which card a message means ---------------------------------------------------------------

_CARD_COLS = "id, bank, last4, statement_day, due_day, billing_group, active"


def _billing_primary(conn, card):
    """The card whose statement a billing-group member is billed on.

    An add-on card has no statement of its own: ICICI 8013 (Amex add-on) is billed on 1019's
    statement, yet CRED and ICICI name either number in bills and payment confirmations (CRED's
    Oct-2025 bill for "8013" is 1019's Rs 22,017.41). Cards sharing cards.billing_group are one
    bill; its primary is the member that carries the statements (ties: the oldest card)."""
    if not card or not card["billing_group"]:
        return card
    members = conn.execute(
        f"SELECT {', '.join('c.' + col for col in _CARD_COLS.split(', '))}, "
        "(SELECT COUNT(*) FROM statements s WHERE s.card_id = c.id) AS n "
        "FROM cards c WHERE c.active=1 AND c.billing_group=? ORDER BY n DESC, c.id ASC",
        (card["billing_group"],)
    ).fetchall()
    return members[0] if members else card


def _resolve_card(conn, last4, active_only=True):
    """The card a message's last-4 refers to — matched directly or via its statement-PDF alias
    (SBI prints 9181 for 2111) and lifted to its billing-group primary."""
    if not last4:
        return None
    row = conn.execute(
        f"SELECT {_CARD_COLS} FROM cards WHERE (last4=? OR pdf_card_last4=?) "
        + ("AND active=1 " if active_only else "")
        + "ORDER BY (last4=?) DESC, active DESC, id ASC LIMIT 1",
        (last4, last4, last4)
    ).fetchone()
    return _billing_primary(conn, row) if row and row["active"] else row


def _group_last4s(conn, last4):
    """Every last-4 billed together with `last4` (itself plus billing-group members)."""
    row = conn.execute("SELECT billing_group FROM cards WHERE last4=? ORDER BY active DESC LIMIT 1", (last4,)).fetchone()
    if not row or not row["billing_group"]:
        return [last4]
    rows = conn.execute("SELECT last4 FROM cards WHERE billing_group=?", (row["billing_group"],)).fetchall()
    return sorted({r["last4"] for r in rows} | {last4})


def _last4_for_suffix(conn, bank, suffix):
    """Resolve a bank's 2-digit card suffix (SBI 'XX11', Axis 'ending XX20') to the unique
    active card of that bank ending in it; None when no or several cards match."""
    if not suffix:
        return None
    rows = conn.execute(
        "SELECT last4 FROM cards WHERE active=1 AND bank=? AND last4 LIKE ?", (bank, f"%{suffix}")
    ).fetchall()
    return rows[0]["last4"] if len(rows) == 1 else None


def _sbi_last4_for_suffix(conn, suffix):
    """Resolve SBI's 2-digit card suffix (XX11) to the unique active SBI card's last4."""
    return _last4_for_suffix(conn, "SBI", suffix)


# --- Payments -------------------------------------------------------------------------------------

# Payment identity (iteration 203 E8). Reports of one payment arrive through several channels within
# a couple of days (bank SMS, CRED / bank email, the debit on the owner's account, the statement line),
# and the bank itself confirms most payments twice in two wordings ("PAYMENT OF Rs. X RECEIVED
# TOWARDS YOUR CREDIT CARD ..." then "Online Payment of Rs.X vide Ref# ... was credited to your card").
# Two reports are nevertheless two payments when they are distinct messages of the same kind: two
# references of the same namespace that differ, two lines of the same statement, or two bank SMS of the
# same wording whose text differs (another date or available limit) — the old "+/-2 days = one payment"
# rule dropped a genuine second Rs 5,000 payment and left the card looking under-paid.
SAME_PAYMENT_DAYS = 2
# A statement prints a payment up to 4 days before / 1 day after the bank's SMS or CRED date
# (127 statement payment lines on 2026-10-20: offsets -4..+1, 106 the same day).
STATEMENT_PAYMENT_DAYS = 4
# The same SMS stored twice (an import of a message the forwarder already sent) is ~1 s apart.
SAME_MESSAGE_SECONDS = 600
_SMS_REF = re.compile(r"\b(?:Ref(?:erence)?|UTR|Txn)\b\s*(?:#|No\.?|ID)?\s*[:#]?\s*([A-Za-z0-9]{6,})", re.I)
_SMS_STATE = re.compile(r"available\s*(?:credit\s*)?limit|avl\.?\s*(?:lmt|limit|bal)|balance", re.I)


def _payment_template(body):
    """Which payment wording an SMS uses (an index into the patterns parse_payment_sms tries)."""
    for i, pattern in enumerate([SBI_BBPS_PATTERN, SBI_AUTO_DEBIT_PATTERN, *CC_PAYMENT_PATTERNS]):
        if pattern.search(body or ""):
            return i
    return None


def _sms_epoch(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00").replace(" ", "T")[:26]).timestamp()
    except (TypeError, ValueError):
        return None


def _sms_reports_differ(conn, sms_a, sms_b):
    """True when two card-payment SMS are evidence of two different payments."""
    rows = {r["id"]: r for r in conn.execute(
        "SELECT id, body, phone_timestamp, received_at FROM sms WHERE id IN (?, ?)", (sms_a, sms_b))}
    a, b = rows.get(sms_a), rows.get(sms_b)
    if not a or not b:
        return False                        # nothing left to compare: one payment, as before
    ref_a, ref_b = (_SMS_REF.search(a["body"] or ""), _SMS_REF.search(b["body"] or ""))
    if ref_a and ref_b:
        return ref_a.group(1).upper() != ref_b.group(1).upper()
    if _payment_template(a["body"]) != _payment_template(b["body"]):
        return False                        # the bank's two wordings of one payment
    text_a, text_b = (" ".join((x["body"] or "").split()).lower() for x in (a, b))
    if text_a != text_b:
        return True                         # same wording, another date / available limit
    if _SMS_STATE.search(text_a):
        return False                        # identical text down to the limit: the same message
    ta, tb = _sms_epoch(a["phone_timestamp"] or a["received_at"]), _sms_epoch(b["phone_timestamp"] or b["received_at"])
    return ta is not None and tb is not None and abs(ta - tb) > SAME_MESSAGE_SECONDS


def _ref_namespace(ref):
    return ref.split(":", 1)[0] if ref and ":" in ref else None


def _stmt_file(stmt_ref):
    return (stmt_ref or "").rsplit("#", 1)[0]


def _reports_differ(conn, cand, sms_id, ref, stmt_ref):
    """Is a new report (sms_id / ref / stmt_ref) evidence of a payment other than `cand`?"""
    if ref and cand["ref"] and _ref_namespace(ref) == _ref_namespace(cand["ref"]) and ref != cand["ref"]:
        return True
    if stmt_ref and cand["stmt_ref"]:
        # two lines of one statement are two payments; the same line in a re-downloaded copy is one
        return _stmt_file(stmt_ref) == _stmt_file(cand["stmt_ref"])
    if sms_id and cand["sms_id"] and sms_id != cand["sms_id"]:
        return _sms_reports_differ(conn, sms_id, cand["sms_id"])
    return False


def _same_payment(conn, group, amount, payment_date_iso, sms_id=None, email_id=None, ref=None, stmt_ref=None,
                  exclude_ids=()):
    """The recorded payment this report is a copy of, or None (see 'Payment identity' above).
    `exclude_ids`: payments a dry run has already matched to other lines of the same statement."""
    marks = ",".join("?" * len(group))
    for col, val in (("sms_id", sms_id), ("email_id", email_id), ("ref", ref), ("stmt_ref", stmt_ref)):
        if val is not None:
            row = conn.execute(f"SELECT * FROM cc_payments WHERE card_last4 IN ({marks}) AND {col} = ? LIMIT 1",
                               (*group, val)).fetchone()
            if row:
                return row
    days = STATEMENT_PAYMENT_DAYS if stmt_ref else SAME_PAYMENT_DAYS
    cands = conn.execute(
        f"SELECT * FROM cc_payments WHERE card_last4 IN ({marks}) AND ABS(amount - ?) < 0.005 "
        "AND ABS(julianday(payment_date) - julianday(?)) <= ? "
        "ORDER BY ABS(julianday(payment_date) - julianday(?)), id",
        (*group, amount, payment_date_iso, days, payment_date_iso)).fetchall()
    for cand in cands:
        if cand["id"] not in exclude_ids and not _reports_differ(conn, cand, sms_id, ref, stmt_ref):
            return cand
    return None


def record_card_payment(conn, card_last4, amount, payment_date_iso, source, sms_id=None,
                        email_id=None, ref=None, raw_text=None, stmt_ref=None):
    """iter-18 contract 2: the one way a card-bill payment is recorded.

    Idempotent: a report of a payment already recorded — the same card (or a card billed with it),
    the same amount within +/-2 days (+/-4 for a statement line), whichever channel reported it first
    (bank SMS, ICICI email, CRED email, the debit on the owner's bank account, the statement's payment
    line) — only fills evidence the row lacks (sms_id / email_id / ref / stmt_ref). A distinct message
    of the same kind is a second payment (iteration 203 E8, see 'Payment identity'). Payments are
    filed on the billing-group primary so an add-on's confirmation counts against the statement it
    pays. Returns {'status': 'inserted'|'exists'|'invalid'|'unknown_card', 'id', 'card_last4'}."""
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        return {"status": "invalid"}
    if not card_last4 or amount <= 0 or not payment_date_iso:
        return {"status": "invalid"}
    card = _resolve_card(conn, str(card_last4)) or _resolve_card(conn, str(card_last4), active_only=False)
    if not card:
        # A last-4 that is no card of the owner's (a misparse, someone else's card paid via CRED)
        # would only ever be noise in cc_payments; the hourly backfill re-reads every message,
        # so a card added later picks its history up then.
        return {"status": "unknown_card"}
    last4 = card["last4"]
    group = _group_last4s(conn, last4)
    existing = _same_payment(conn, group, amount, payment_date_iso, sms_id, email_id, ref, stmt_ref)
    if existing:
        conn.execute(
            "UPDATE cc_payments SET sms_id=COALESCE(sms_id, ?), email_id=COALESCE(email_id, ?), "
            "ref=COALESCE(ref, ?), stmt_ref=COALESCE(stmt_ref, ?) WHERE id=?",
            (sms_id, email_id, ref, stmt_ref, existing["id"])
        )
        return {"status": "exists", "id": existing["id"], "card_last4": last4}
    cur = conn.execute(
        "INSERT INTO cc_payments (card_last4, amount, payment_date, source, sms_id, email_id, ref, stmt_ref, "
        "raw_text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (last4, amount, payment_date_iso, source, sms_id, email_id, ref, stmt_ref, (raw_text or "")[:500] or None)
    )
    return {"status": "inserted", "id": cur.lastrowid, "card_last4": last4}


def _payment_date_from_sms(body, phone_timestamp, received_at=None):
    """The payment date an SMS states (DD-MM-YY[YY] / DD/MM/YYYY), else the IST day it arrived.
    The fallback used to be the UTC date of phone_timestamp, which put a payment confirmed
    between 00:00 and 05:30 IST on the previous day. (iter-18 M8)"""
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})", body)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        if len(y) == 2:
            y = "20" + y
        try:
            return date(int(y), int(mo), int(d)).isoformat()
        except ValueError:
            pass
    return ist_date_of(phone_timestamp or received_at)


def sms_payments_not_recorded(conn=None):
    """Read-only: card-payment SMS the payment-identity rules would record as a payment of their own
    on the next backfill (a genuine second same-amount payment the old +/-2-day rule had folded into
    the first). The iteration-203 post-deploy dry run lists these. (E8)"""
    conn = conn or get_db()
    load_pdf_aliases(conn)
    out = []
    for sms in _payment_sms_rows(conn):
        result = parse_payment_sms(sms["body"])
        if not (result and result.get("card_last4")):
            continue
        payment_date = _payment_date_from_sms(sms["body"], sms["phone_timestamp"], sms["received_at"])
        card = _resolve_card(conn, normalize_card_last4(result["card_last4"])) or \
            _resolve_card(conn, normalize_card_last4(result["card_last4"]), active_only=False)
        if not payment_date or not card:
            continue
        group = _group_last4s(conn, card["last4"])
        if not _same_payment(conn, group, round(float(result["amount"]), 2), payment_date, sms_id=sms["id"]):
            out.append({"sms_id": sms["id"], "card_last4": card["last4"], "amount": result["amount"],
                        "payment_date": payment_date})
    return out


def _payment_sms_rows(conn):
    return conn.execute(
        "SELECT id, body, phone_timestamp, received_at FROM sms WHERE "
        "body LIKE '%PAYMENT%RECEIVED%CARD%' OR "
        "body LIKE '%Online Payment%credited%card%' OR "
        "body LIKE '%Payment%received%Card%' OR "
        "body LIKE '%payment%received%Credit Card%' OR "
        "body LIKE '%thank you for%payment%card%' OR "
        "body LIKE '%received a payment%credit card%' OR "
        "body LIKE '%Payment of INR%received%Card%' OR "
        "body LIKE '%Payment of Rs%received%Card%' OR "
        "body LIKE '%received payment%BBPS%SBI Credit Card%' OR "
        "body LIKE '%received payment%Auto Debit%SBI Card%' "
        "ORDER BY id"
    ).fetchall()


def backfill_payments_logic(conn=None):
    conn = conn or get_db()
    load_pdf_aliases(conn)
    rows = _payment_sms_rows(conn)
    inserted = 0
    for sms in rows:
        result = parse_payment_sms(sms["body"])
        if not (result and result.get("card_last4")):
            continue
        payment_date = _payment_date_from_sms(sms["body"], sms["phone_timestamp"], sms["received_at"])
        if not payment_date:
            continue
        res = record_card_payment(conn, normalize_card_last4(result["card_last4"]), result["amount"],
                                  payment_date, "sms", sms_id=sms["id"], raw_text=sms["body"])
        inserted += res["status"] == "inserted"
    conn.commit()
    # iter-18 M1: bills paid through CRED often produce no bank SMS at all; CRED's own
    # confirmation email (and ICICI's) is the evidence. Chained here so the hourly
    # cards_maintenance task picks them up without a new task type.
    emails = payments_from_emails_logic(conn)
    return {"message": f"Backfilled {inserted} CC payments from {len(rows)} candidate SMS", "emails": emails}


def payments_from_emails_logic(conn=None):
    """iter-18 M1: card-bill payments confirmed by email — CRED 'your credit card bill payment was
    successful' (source 'email-cred') and ICICI 'Payment received on your ICICI Bank Credit Card'
    (source 'email-bank')."""
    from modules.email import bill_emails as be
    conn = conn or get_db()
    senders = sorted(be.PAYMENT_SENDERS)
    rows = conn.execute(
        f"SELECT id, sender_email, subject, date, body_snippet FROM emails WHERE lower(sender_email) IN "
        f"({','.join('?' * len(senders))}) AND (subject LIKE '%payment was successful%' OR subject LIKE '%Payment received%')",
        senders
    ).fetchall()
    found = inserted = 0
    for r in rows:
        p = be.parse_payment_email(r["sender_email"], r["subject"], r["body_snippet"], be.email_ist_date(r["date"]))
        if not p:
            continue
        found += 1
        res = record_card_payment(conn, p["card_last4"], p["amount"], p["payment_date"],
                                  "email-cred" if p["authority"] == "cred" else "email-bank",
                                  email_id=r["id"], ref=p["ref"], raw_text=be.clean_text(r["body_snippet"]))
        inserted += res["status"] == "inserted"
    conn.commit()
    return {"emails": len(rows), "payments_found": found, "inserted": inserted}


def backfill_statements_logic(conn=None):
    conn = conn or get_db()
    cards = conn.execute(f"SELECT {_CARD_COLS} FROM cards WHERE active=1").fetchall()
    inserted = 0
    for card in cards:
        # An add-on card (ICICI 8013 on 1019's statement) has no statement of its own: since the
        # iter-18 re-parse files add-on lines under the add-on's number, its lines are rolled into
        # the primary's statement here instead of minting a separate one.
        if _billing_primary(conn, card)["id"] != card["id"]:
            continue
        stmt_day = card["statement_day"] or 1
        card_id = card["id"]
        group = _group_last4s(conn, card["last4"])
        marks = ",".join("?" * len(group))
        txns = conn.execute(
            "SELECT date, amount, txn_type FROM transactions "
            f"WHERE source='card' AND card_last4 IN ({marks}) AND is_duplicate=0 ORDER BY date",
            group
        ).fetchall()
        if not txns:
            continue
        periods = defaultdict(lambda: {"total": 0, "count": 0, "start": None, "end": None})
        for t in txns:
            date_str = t["date"]
            if not date_str or len(date_str) < 10:
                continue
            try:
                year, month, day = int(date_str[:4]), int(date_str[5:7]), int(date_str[8:10])
            except (ValueError, IndexError):
                continue
            if day <= stmt_day:
                stmt_month, stmt_year = month, year
            else:
                stmt_month = month + 1
                stmt_year = year
                if stmt_month > 12:
                    stmt_month = 1
                    stmt_year += 1
            # billing_end/start use the raw stmt_month (closing month)
            billing_end = f"{stmt_year}-{stmt_month:02d}-{stmt_day:02d}"
            prev_month = stmt_month - 1
            prev_year = stmt_year
            if prev_month < 1:
                prev_month = 12
                prev_year -= 1
            billing_start = f"{prev_year}-{prev_month:02d}-{stmt_day + 1:02d}"
            # For stmt_day=1, label by spending month (prev_month) not closing month
            # e.g. period Jan 2 - Feb 1 is labeled January, not February
            if stmt_day == 1:
                stmt_month -= 1
                if stmt_month < 1:
                    stmt_month = 12
                    stmt_year -= 1
            key = f"{stmt_year}-{stmt_month:02d}"
            amt = abs(t["amount"]) if t["txn_type"] == "debit" else 0
            periods[key]["total"] += amt
            periods[key]["count"] += 1
            periods[key]["start"] = billing_start
            periods[key]["end"] = billing_end
        for period_key, data in periods.items():
            year, month = period_key.split("-")
            year, month = int(year), int(month)
            exists = conn.execute(
                "SELECT id FROM statements WHERE card_id=? AND month=? AND year=?",
                (card_id, month, year)
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO statements (card_id, month, year, total_amount, transaction_count, "
                    "billing_start, billing_end, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'parsed')",
                    (card_id, month, year, round(data["total"], 2), data["count"],
                     data.get("start"), data.get("end"))
                )
                inserted += 1
    conn.commit()
    return {"message": f"Backfilled {inserted} statement records for {len(cards)} cards"}


MONTH_MAP = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
             "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
             "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
             "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _months_ago(year, month, n):
    """(year, month) shifted back n months."""
    idx = year * 12 + (month - 1) - n
    return idx // 12, idx % 12 + 1


def _statement_window_for_due(due_iso, stmt_day, due_day):
    """The billing cycle a bill with this due date is for. (iter-16 C1)

    The statement closes on `stmt_day`; the bill is due `due_day` later. billing_end is
    therefore the most recent stmt_day ON OR BEFORE the due date: if stmt_day <= due_day
    the statement closed earlier in the SAME month as the due date; otherwise it closed
    the previous month. Verified against real HDFC data: for a card that closes on the 1st
    and is due on the 21st, due-21-Apr -> window Mar02..Apr01 sums to the printed total
    exactly. Returns (billing_start, billing_end, close_year, close_month).
    """
    y, m, d = int(due_iso[:4]), int(due_iso[5:7]), int(due_iso[8:10])
    if stmt_day <= due_day:
        close_year, close_month = y, m
    else:
        close_year, close_month = _months_ago(y, m, 1)
    billing_end = f"{close_year}-{close_month:02d}-{min(stmt_day, 28):02d}"
    sy, sm = _months_ago(close_year, close_month, 1)
    billing_start = f"{sy}-{sm:02d}-{min(stmt_day + 1, 28):02d}"
    return billing_start, billing_end, close_year, close_month


def _statement_window_for_close(issued_iso, stmt_day):
    """The billing cycle of a statement message that prints no due date ("Pay by Nil", "NO
    PAYMENT REQUIRED", HDFC's dateless "Amt Due" reminder): the most recent close (stmt_day) on
    or before the IST day the message was issued. Same window/label convention as the due-date
    path, so both paths land on the same statement row. (iter-18 H1/HP5)"""
    y, m, d = int(issued_iso[:4]), int(issued_iso[5:7]), int(issued_iso[8:10])
    sd = min(stmt_day, 28)
    close_year, close_month = (y, m) if d >= sd else _months_ago(y, m, 1)
    billing_end = f"{close_year}-{close_month:02d}-{sd:02d}"
    sy, sm = _months_ago(close_year, close_month, 1)
    billing_start = f"{sy}-{sm:02d}-{min(stmt_day + 1, 28):02d}"
    return billing_start, billing_end, close_year, close_month


# Who said it, strongest first (iter-18 HP5). A statement value is only replaced by a source at
# least as strong; equal strength = the newest message wins (callers process oldest first).
#   statement — the issuer's statement message (SMS, statement email, statement PDF)
#   reminder  — the issuer's later "amount due" nudge: it can reflect a revision or a part-payment,
#               so it never overrides a statement figure, only fills a missing one
#   cred      — CRED relaying the bill; the bank's own figure always wins over it
_RANK_CRED, _RANK_REMINDER, _RANK_STATEMENT = 1, 2, 3
_DUE_RANK = {"cred": _RANK_CRED, "bank": _RANK_STATEMENT}


def _source_rank(source):
    tokens = set(re.split(r"[-_\s]+", (source or "").lower())) - {""}
    if not tokens:
        return 0
    if "cred" in tokens:
        return _RANK_CRED
    if "reminder" in tokens:
        return _RANK_REMINDER
    return _RANK_STATEMENT


def _upsert_stated_statement(conn, card_last4, total_due, due_iso, source, min_due=None,
                             close_iso=None, fill_only=False):
    """Record the bank's PRINTED total-amount-due as the authoritative statement total,
    on the statement row for the cycle this bill is actually for. (iter-16 C1)

    Uses the verified due-date->cycle math (not a naive due_month-1) so stated_total and
    the parsed-transaction sum describe the SAME period, and labels the row with the SAME
    (month, year) convention as backfill_statements_logic (spending month for stmt_day==1
    cards, close month otherwise) so the exact-key lookup below UPDATEs backfill's row
    rather than inserting a duplicate. A statement backfill happened to mislabel under a
    different key is later collapsed by reconcile_statements_logic's exact-window dedup.

    iter-18 H1/HP5: also keeps the bank's due date (due_date, due_date_source 'bank'|'cred') and
    min_due, and applies source precedence (_source_rank). `total_due=None` updates only the due
    date of an existing statement (CRED "Remaining due"); `close_iso` places a message with no due
    date; `fill_only` writes the total only over a weaker source (HDFC's dateless reminder).
    A negative total is a credit balance. Returns 1 on a create or a stated_total/due_date change.
    """
    card = _resolve_card(conn, card_last4)
    if not card:
        return 0
    card_id = card["id"]
    stmt_day = card["statement_day"] or 1
    due_day = card["due_day"] or 20
    if due_iso:
        billing_start, billing_end, cy, cm = _statement_window_for_due(due_iso, stmt_day, due_day)
    elif close_iso:
        billing_start, billing_end, cy, cm = _statement_window_for_close(close_iso, stmt_day)
    else:
        return 0

    # Match backfill_statements_logic's (month, year) labelling so we UPDATE the same row
    # rather than colliding on the UNIQUE(card_id, month, year) key: backfill labels a
    # statement_day==1 card by its SPENDING month (the month before close), everything
    # else by the close month. We keep the correct WINDOW regardless.
    if stmt_day == 1:
        label_year, label_month = _months_ago(cy, cm, 1)
    else:
        label_year, label_month = cy, cm

    rank = _source_rank(source)
    due_origin = "cred" if rank == _RANK_CRED else "bank"
    exists = conn.execute(
        "SELECT id, stated_total, total_source, due_date, due_date_source, min_due FROM statements "
        "WHERE card_id=? AND month=? AND year=?",
        (card_id, label_month, label_year)
    ).fetchone()

    if exists:
        held = _source_rank(exists["total_source"]) if exists["stated_total"] is not None else 0
        take_total = total_due is not None and (rank > held or (rank == held and not fill_only))
        held_due = _DUE_RANK.get(exists["due_date_source"], _RANK_STATEMENT) if exists["due_date"] else 0
        take_due = bool(due_iso) and _DUE_RANK[due_origin] >= held_due
        # Always rewrite the window: the bank's date is authoritative and this also heals rows
        # written by an earlier, wrong window derivation.
        sets, params = ["billing_start=?", "billing_end=?"], [billing_start, billing_end]
        changed = False
        if take_total:
            sets += ["stated_total=?", "total_amount=?", "total_source=?"]
            params += [total_due, total_due, source]
            changed |= exists["stated_total"] is None or abs(exists["stated_total"] - total_due) > 0.005
        if min_due is not None and (take_total or exists["min_due"] is None):
            sets.append("min_due=?")
            params.append(min_due)
        if take_due:
            sets += ["due_date=?", "due_date_source=?"]
            params += [due_iso, due_origin]
            changed |= exists["due_date"] != due_iso
        conn.execute(f"UPDATE statements SET {', '.join(sets)} WHERE id=?", (*params, exists["id"]))
        return 1 if changed else 0

    if total_due is None:
        return 0  # a due-date-only message never invents a statement
    txn_count = conn.execute(
        "SELECT COUNT(*) as c FROM transactions WHERE source='card' AND card_last4=? AND is_duplicate=0 "
        "AND date >= ? AND date <= ?",
        (card["last4"], billing_start, billing_end)
    ).fetchone()["c"]
    conn.execute(
        "INSERT INTO statements (card_id, month, year, total_amount, stated_total, total_source, min_due, "
        "due_date, due_date_source, transaction_count, billing_start, billing_end, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (card_id, label_month, label_year, total_due, total_due, source, min_due,
         due_iso, due_origin if due_iso else None, txn_count, billing_start, billing_end,
         (source or "sms").split("-")[0])
    )
    return 1


def record_bank_statement(conn, card_last4, total_due, due_date_iso, source, min_due=None,
                          statement_date_iso=None):
    """iter-18 contract 1: record a bank-printed statement total (statement PDF, SMS, email).

    Stores stated_total, the bank's due date (due_date_source='bank') and min_due on the statement
    row for that cycle; a negative total is a credit balance. `card_last4` may be the card's own
    last-4 or its statement-PDF alias. `statement_date_iso` places a statement that prints no due
    date. Returns 1 when something changed, else 0."""
    if total_due is None:
        raise ValueError("total_due is required")
    return _upsert_stated_statement(
        conn, str(card_last4), round(float(total_due), 2), due_date_iso, source or "bank",
        min_due=None if min_due is None else round(float(min_due), 2), close_iso=statement_date_iso)


# Statement-alert SMS across banks. Each yields (card_last4, total_due, due_month, due_year).
# These carry the bank's PRINTED total in plain text — far more reliable than summing
# parsed PDF lines, which can only undercount. (iter-16 C1)
_STMT_HDFC = re.compile(
    r"HDFC Bank Credit Card XX(\d{4}) Statement.*?Total due:\s*Rs\.?\s*([\d,]+\.?\d*).*?Pay by\s*(\d{2})-(\d{2})-(\d{4})",
    re.IGNORECASE | re.DOTALL)
_STMT_HDFC2 = re.compile(
    r"HDFC Bank Credit Card XX(\d{4}) Statement:\s*Total due amt:\s*Rs\.?\s*([\d,]+\.?\d*).*?Due by:\s*(\d{2})-(\d{2})-(\d{4})",
    re.IGNORECASE | re.DOTALL)
# iter-18 H1: a zero / credit-balance HDFC statement prints no due date: "Total due: Rs.-0.46
# Min.due: Rs.0.00 Pay by Nil". Groups: (last4, total).
_STMT_HDFC_NIL = re.compile(
    r"HDFC Bank Credit Card XX(\d{4}) Statement:?\s*Total due:\s*Rs\.?\s*(-?[\d,]+\.?\d*).*?Pay by\s*Nil",
    re.IGNORECASE | re.DOTALL)
_STMT_CRED = re.compile(
    r"credit card bill for (?:SBI\s*|ICICI Bank\s*|HDFC\s*|Axis\s*)?(?:Bank\s*)?(?:XXXX-)?(\d{4}).*?Total amount:\s*INR\s*([\d,]+\.?\d*).*?Due date:\s*(\w+)\s*(\d{2}),?\s*(\d{4})",
    re.IGNORECASE | re.DOTALL)
# Axis prints "INR  Dr. 1051.38" for an amount owed and "INR  Cr. 199.00" for a credit balance
# (iter-18 M15); the Dr/Cr marker is non-capturing so the group numbers stay as they were and
# the sign is read by _axis_credit().
_STMT_AXIS = re.compile(
    r"Axis Bank Credit Card no\.\s*XX(\d{4}).*?Total amount due:\s*INR\s*(?:(?:Dr|Cr)\.?\s*)?([\d,]+\.?\d*).*?(\d{2})[-/](\d{2})[-/](\d{2,4})",
    re.IGNORECASE | re.DOTALL)
# ICICI "Pay Total Due of Rs {total} or Minimum Due Rs {min} by {DD-Mon-YY} for ICICI Bank Credit
# Card XX{last4}". Total, THEN date, THEN card — the first cut assumed total->card->date and
# matched 0 of 46 real bodies. Anchored on "Pay Total Due ... for ICICI" so reversal/overdue/
# "Revised total due" notices (which are mid-cycle, not statements) never match.
# Groups: (total, dd, mon, yy, last4). iter-18: this arrives a week before the due date, after the
# statement SMS, and can carry a revised figure (0026 Oct-26: Rs 16,460.31 vs the statement's
# 16,469.81), so it is a reminder, not a statement total.
_STMT_ICICI = re.compile(
    r"Pay Total Due of Rs\.?\s*([\d,]+\.?\d*)\s*or Minimum Due Rs\.?\s*[\d,]+\.?\d*\s*"
    r"by\s*(\d{2})-(\w{3})-(\d{2,4})\s*for ICICI Bank Credit Card\s*(?:XX)?(\d{4})",
    re.IGNORECASE)
_STMT_HSBC = re.compile(
    r"total payment due on HSBC credit card ending with\s*(\d{4})\s*is INR\s*([\d,]+\.?\d*).*?due date\s*(\d{2})-(\w{3})-(\d{2,4})",
    re.IGNORECASE | re.DOTALL)
# iter-17 P3: formats the iter-16 set never matched (no stated total after 2026-06).
# Axis: "[Your ]statement for [your ]Axis Bank Credit Card no. XX9320 is/has been generated.
# Due on: 02-10-26 Total amt: INR Dr. 1527.19" — date BEFORE total. Groups: (last4, dd, mm, yy, total).
# iter-18 M15: also "Total amt: INR  Cr. 199.00" (credit balance).
_STMT_AXIS2 = re.compile(
    r"statement for (?:your )?Axis Bank Credit Card no\.\s*XX(\d{4}).*?Due on:\s*(\d{2})-(\d{2})-(\d{2,4})\s*"
    r"Total amt:\s*INR\s*(?:(?:Dr|Cr)\.?\s*)?([\d,]+\.?\d*)",
    re.IGNORECASE | re.DOTALL)
# ICICI: "ICICI Bank Credit Card XX0026 Statement is sent to ... Total of Rs 16,469.81 or minimum
# of Rs 5,440.00 is due by 08-OCT-26." Groups: (last4, total, dd, mon, yy).
_STMT_ICICI2 = re.compile(
    r"ICICI Bank Credit Card XX(\d{4}) Statement is sent.*?Total of Rs\.?\s*([\d,]+\.?\d*)\s*or minimum of Rs\.?\s*"
    r"[\d,]+\.?\d*\s*is due by\s*(\d{2})-(\w{3})-(\d{2,4})",
    re.IGNORECASE | re.DOTALL)
# SBI: "E-statement of SBI Credit Card ending XX11 dated 19/09/2026 ... Total Amt Due Rs 46088;
# ... Payable by 09/10/2026". Only the last TWO digits are given. Groups: (suffix, total, dd, mm, yyyy).
_STMT_SBI = re.compile(
    r"E-statement of SBI Credit Card ending XX(\d{2,4}).*?Total Amt Due Rs\.?\s*([\d,]+\.?\d*).*?"
    r"Payable by\s*(\d{2})/(\d{2})/(\d{4})",
    re.IGNORECASE | re.DOTALL)
# iter-18 HP5: Axis' 2025 wording "Statement for your Axis Bank Credit Card XX9320 of INR 6298.35 has
# been generated with due date 01-04-25 ... Minimum amount due is INR 705." Groups: (last4, total, dd, mm, yy).
_STMT_AXIS3 = re.compile(
    r"Statement for your Axis Bank Credit Card XX(\d{4}) of INR\s*([\d,]+\.?\d*) has been generated with due date\s*"
    r"(\d{2})-(\d{2})-(\d{2,4})",
    re.IGNORECASE)

# Bank reminders (iter-18 HP5): the issuer's own "amount due" nudge. Statement-strength for the due
# date, reminder-strength for the total (see _source_rank).
# HDFC: "Amount Due\nRs.18691 on HDFC Bank Credit Card 9434. Pay instantly by 11/JUN/2026 via PayZapp"
_REM_HDFC = re.compile(
    r"Amount Due\s*Rs\.?\s*([\d,]+\.?\d*) on HDFC Bank Credit Card\s*(?:XX)?(\d{4})\.?\s*Pay instantly by\s*"
    r"(\d{1,2})/([A-Za-z]{3})/(\d{4})", re.IGNORECASE)
# HDFC, no date: "Amt Due: Rs 11865 on HDFC Bank Credit Card 9434. Pay Instant with PayZapp" and
# "Dear Customer, Amt Due Rs.9987 on HDFC Bank Card X9434 Pay with PayZapp". HDFC sends it the day
# after the statement date; it is filed on the statement that most recently closed on or before
# its IST arrival date and only fills a missing (or CRED-relayed) total — never the due date.
_REM_HDFC_NODATE = re.compile(
    r"Amt Due:?\s*Rs\.?\s*([\d,]+\.?\d*) on HDFC Bank (?:Credit )?Card\s*X{0,2}(\d{4})", re.IGNORECASE)
# Axis: "Payment of INR 2411.32 for Axis Bank Credit Card no. XX9320 is due on 01-08-26 with minimum
# amount due of INR 100. Ignore if paid."
_REM_AXIS = re.compile(
    r"Payment of INR\s*([\d,]+\.?\d*) for Axis Bank Credit Card no\.\s*XX(\d{4}) is due on\s*(\d{2})-(\d{2})-(\d{2,4})",
    re.IGNORECASE)
# ICICI: "Pay Total Amount Due of Rs 16,460.31 or Minimum Amount Due of Rs 5,430.50 by 08-Oct-26 towards
# ICICI Bank Credit Card XX0026" and "Kindly pay total due of Rs 33366.5 or Min Due Rs 1670 by 23-Feb-25
# on ICICI Bank Credit Card XX0026". Groups: (total, min, dd, mon, yy, last4).
_REM_ICICI = re.compile(
    r"(?:Pay|Kindly pay) Total (?:Amount )?Due of Rs\.?\s*([\d,]+\.?\d*)\s*or Min(?:imum)? (?:Amount )?Due (?:of )?"
    r"Rs\.?\s*([\d,]+\.?\d*)\s*by\s*(\d{2})-([A-Za-z]{3})-(\d{2,4})\s*(?:for|towards|on) ICICI Bank Credit Card\s*(?:XX)?(\d{4})",
    re.IGNORECASE)
# SBI: "Dear SBI Cardholder, outstanding of Rs. 1488.00, on your credit card ending 2111 is due on
# 08-SEP-25. Min. Amount Due: Rs. 200.00."
_REM_SBI = re.compile(
    r"outstanding of Rs\.?\s*([\d,]+\.?\d*),?\s*on your credit card ending\s*(\d{4}) is due on\s*(\d{2})-([A-Za-z]{3})-(\d{2,4})",
    re.IGNORECASE)

# Minimum due, per wording (iter-18 H1).
_MIN_HDFC = re.compile(r"Min\.?\s*due(?:\s*amt)?:\s*Rs\.?\s*(-?[\d,]+\.?\d*)", re.IGNORECASE)
_MIN_AXIS = re.compile(
    r"Min(?:imum)?\s*(?:amt|amount)\s*due(?:\s*is|\s*of)?:?\s*INR\s*(?:(?:Dr|Cr)\.?\s*)?([\d,]+(?:\.\d+)?)", re.IGNORECASE)
_MIN_ICICI = re.compile(
    r"or Min(?:imum)? (?:Amount )?Due (?:of )?Rs\.?\s*([\d,]+\.?\d*)|or minimum of Rs\.?\s*([\d,]+\.?\d*)", re.IGNORECASE)
_MIN_SBI = re.compile(r"Min(?:\.|imum)?\s*(?:Amt|Amount) Due:?\s*Rs\.?\s*([\d,]+\.?\d*)", re.IGNORECASE)
_MIN_HSBC = re.compile(r"minimum payment due is INR\s*([\d,]+\.?\d*)", re.IGNORECASE)


def _num(s):
    return float(str(s).replace(",", "").rstrip("."))


def _min_from(regex, body):
    m = regex.search(body)
    if not m:
        return None
    val = next((g for g in m.groups() if g), None)
    return _num(val) if val else None


def _axis_credit(match_text, label):
    """True when Axis printed the amount as a credit balance ('INR  Cr. 199.00')."""
    return re.search(label + r":?\s*INR\s*Cr\b", match_text, re.IGNORECASE) is not None


def _yr(y):
    return int(y) if len(str(y)) == 4 else 2000 + int(y)


def _iso(d, mon, y):
    """Build YYYY-MM-DD from a numeric day, a numeric-or-name month, and a 2/4-digit year."""
    mm = int(mon) if str(mon).isdigit() else MONTH_MAP.get(str(mon).lower())
    if not mm:
        return None
    try:
        return date(_yr(y), mm, int(d)).isoformat()
    except ValueError:
        return None


def parse_statement_sms(body):
    """Statement facts from one SMS, or None.

    Returns {'last4' | 'suffix', 'total' (None = not stated), 'due' (ISO or None), 'min_due',
    'source', 'fill_only'}. A fact with no due date is placed by the SMS's arrival date."""
    def fact(last4, total, due, source, min_due=None, fill_only=False, suffix=None):
        return {"last4": last4, "suffix": suffix, "total": total, "due": due, "min_due": min_due,
                "source": source, "fill_only": fill_only}

    m = _STMT_HDFC.search(body) or _STMT_HDFC2.search(body)
    if m:  # groups: last4, total, dd, mm, yyyy
        return fact(m.group(1), _num(m.group(2)), _iso(m.group(3), m.group(4), m.group(5)), "sms-hdfc",
                    _min_from(_MIN_HDFC, body))
    m = _STMT_HDFC_NIL.search(body)
    if m:  # groups: last4, total (zero or a credit balance)
        return fact(m.group(1), _num(m.group(2)), None, "sms-hdfc", _min_from(_MIN_HDFC, body))
    m = _STMT_CRED.search(body)
    if m:  # groups: last4, total, monthname, dd, yyyy
        return fact(m.group(1), _num(m.group(2)), _iso(m.group(4), m.group(3), m.group(5)), "sms-cred")
    m = _STMT_AXIS.search(body)
    if m:  # groups: last4, total, dd, mm, yy
        total = _num(m.group(2))
        return fact(m.group(1), -total if _axis_credit(m.group(0), "Total amount due") else total,
                    _iso(m.group(3), m.group(4), m.group(5)), "sms-axis", _min_from(_MIN_AXIS, body))
    m = _STMT_ICICI.search(body)
    if m:  # groups: total, dd, mon, yy, last4
        return fact(m.group(5), _num(m.group(1)), _iso(m.group(2), m.group(3), m.group(4)),
                    "sms-icici-reminder", _min_from(_MIN_ICICI, body))
    m = _STMT_HSBC.search(body)
    if m:  # groups: last4, total, dd, mon, yy
        return fact(m.group(1), _num(m.group(2)), _iso(m.group(3), m.group(4), m.group(5)), "sms-hsbc",
                    _min_from(_MIN_HSBC, body))
    m = _STMT_AXIS2.search(body)
    if m:  # groups: last4, dd, mm, yy, total
        total = _num(m.group(5))
        return fact(m.group(1), -total if _axis_credit(m.group(0), "Total amt") else total,
                    _iso(m.group(2), m.group(3), m.group(4)), "sms-axis", _min_from(_MIN_AXIS, body))
    m = _STMT_ICICI2.search(body)
    if m:  # groups: last4, total, dd, mon, yy
        return fact(m.group(1), _num(m.group(2)), _iso(m.group(3), m.group(4), m.group(5)), "sms-icici",
                    _min_from(_MIN_ICICI, body))
    m = _STMT_SBI.search(body)
    if m:  # groups: suffix, total, dd, mm, yyyy
        return fact(None, _num(m.group(2)), _iso(m.group(3), m.group(4), m.group(5)), "sms-sbi",
                    _min_from(_MIN_SBI, body), suffix=m.group(1))
    m = _STMT_AXIS3.search(body)
    if m:  # groups: last4, total, dd, mm, yy
        return fact(m.group(1), _num(m.group(2)), _iso(m.group(3), m.group(4), m.group(5)), "sms-axis",
                    _min_from(_MIN_AXIS, body))
    m = _REM_HDFC.search(body)
    if m:  # groups: total, last4, dd, mon, yyyy
        return fact(m.group(2), _num(m.group(1)), _iso(m.group(3), m.group(4), m.group(5)), "sms-hdfc-reminder")
    m = _REM_HDFC_NODATE.search(body)
    if m:  # groups: total, last4
        return fact(m.group(2), _num(m.group(1)), None, "sms-hdfc-reminder", fill_only=True)
    m = _REM_AXIS.search(body)
    if m:  # groups: total, last4, dd, mm, yy
        return fact(m.group(2), _num(m.group(1)), _iso(m.group(3), m.group(4), m.group(5)), "sms-axis-reminder",
                    _min_from(_MIN_AXIS, body))
    m = _REM_ICICI.search(body)
    if m:  # groups: total, min, dd, mon, yy, last4
        return fact(m.group(6), _num(m.group(1)), _iso(m.group(3), m.group(4), m.group(5)), "sms-icici-reminder",
                    _num(m.group(2)))
    m = _REM_SBI.search(body)
    if m:  # groups: total, last4, dd, mon, yy
        return fact(m.group(2), _num(m.group(1)), _iso(m.group(3), m.group(4), m.group(5)), "sms-sbi-reminder",
                    _min_from(_MIN_SBI, body))
    return None


def statements_from_sms_logic(conn=None):
    """Create/refresh statement records from bank statement-alert SMS, capturing the
    bank's printed Total Amount Due as the authoritative stated_total. (iter-16 C1)

    iter-18 H1/HP5: also the bank's due date and minimum due, credit-balance statements, the
    banks' "amount due" reminders, and — chained at the end — bank and CRED bill emails."""
    conn = conn or get_db()
    rows = conn.execute(
        "SELECT id, body, phone_timestamp, received_at FROM sms WHERE "
        "body LIKE '%Statement%Total due%' OR body LIKE '%statement generated%Total%' OR "
        "body LIKE '%credit card bill%Total amount%' OR body LIKE '%Total amount due%' OR "
        "body LIKE '%total due of%' OR body LIKE '%Total of Rs%is due%' OR "
        "body LIKE '%total payment due on HSBC%' OR "
        "body LIKE '%statement for%Axis Bank Credit Card%Total amt%' OR "
        "body LIKE '%E-statement of SBI Credit Card%Total Amt Due%' OR "
        "body LIKE '%Statement for your Axis Bank Credit Card%generated with due date%' OR "
        "body LIKE '%Amount Due%HDFC Bank Credit Card%' OR body LIKE '%Amt Due%HDFC Bank%Card%' OR "
        "body LIKE '%Payment of INR%Axis Bank Credit Card%is due on%' OR "
        "body LIKE '%Total Amount Due of%ICICI%' OR "
        "body LIKE '%outstanding of Rs%credit card ending%is due on%' "
        # Oldest first so, when two SMS state slightly different totals for the same
        # cycle (CRED vs bank, revised bill), the newest deterministically wins. (iter-17)
        "ORDER BY COALESCE(phone_timestamp, received_at), id"
    ).fetchall()

    inserted = 0
    for sms in rows:
        try:
            f = parse_statement_sms(sms["body"])
        except (ValueError, IndexError):
            continue
        if not f:
            continue
        last4 = f["last4"] or _sbi_last4_for_suffix(conn, f["suffix"])
        if not last4:
            continue
        close_iso = None if f["due"] else ist_date_of(sms["phone_timestamp"] or sms["received_at"])
        inserted += _upsert_stated_statement(conn, last4, f["total"], f["due"], f["source"],
                                             min_due=f["min_due"], close_iso=close_iso, fill_only=f["fill_only"])

    conn.commit()
    emails = statements_from_emails_logic(conn)
    return {"message": f"Processed {len(rows)} statement SMS, created/updated {inserted} stated totals",
            "emails": emails}


def statements_from_emails_logic(conn=None):
    """iter-18 HP5: statement facts from issuer statement emails (ICICI, SBI incl. AURUM, Axis) and
    CRED's bill emails. CRED-relayed values never override a bank's; CRED's "Remaining due"
    (what is left after a part-payment) only ever supplies the due date."""
    from modules.email import bill_emails as be
    conn = conn or get_db()
    senders = sorted(be.BILL_SENDERS)
    rows = conn.execute(
        f"SELECT id, sender_email, subject, date, body_snippet FROM emails WHERE lower(sender_email) IN "
        f"({','.join('?' * len(senders))})",
        senders
    ).fetchall()
    facts = []
    for r in rows:
        sent = be.email_ist_date(r["date"])
        f = be.parse_bill_email(r["sender_email"], r["subject"], r["body_snippet"], sent)
        if f:
            facts.append((sent or date.min, r["id"], f))
    # oldest first: within one strength the newest message wins
    facts.sort(key=lambda x: (x[0], x[1]))
    changed = skipped = 0
    for _sent, _eid, f in facts:
        last4 = f.get("card_last4") or _last4_for_suffix(conn, f.get("bank"), f.get("card_suffix"))
        if not last4:
            skipped += 1
            continue
        source = "email-cred" if f["authority"] == "cred" else f"email-{(f.get('bank') or 'bank').lower()}"
        changed += _upsert_stated_statement(
            conn, last4, f["total_due"], f["due_date"], source, min_due=f["min_due"],
            close_iso=None if f["due_date"] else f["statement_date"])
    conn.commit()
    return {"emails": len(rows), "facts": len(facts), "changed": changed, "unresolved_card": skipped}


def _dedup_statement_windows(conn):
    """Collapse statement rows that share an identical (card_id, billing_start, billing_end)
    but were labelled under different (month, year) — an artifact of the two statement paths
    using different labelling for some cards. Keep the row that carries a stated_total (the
    authoritative one); delete the rest. (iter-16 C1) A bank due date / min due held only by a
    deleted row is carried over to the kept one. (iter-18 H1)"""
    dupes = conn.execute(
        "SELECT card_id, billing_start, billing_end FROM statements "
        "WHERE billing_start IS NOT NULL AND billing_end IS NOT NULL "
        "GROUP BY card_id, billing_start, billing_end HAVING COUNT(*) > 1"
    ).fetchall()
    removed = 0
    for d in dupes:
        rows = conn.execute(
            "SELECT id, stated_total, due_date, due_date_source, min_due FROM statements "
            "WHERE card_id=? AND billing_start=? AND billing_end=? "
            "ORDER BY (stated_total IS NOT NULL) DESC, (due_date IS NOT NULL) DESC, id ASC",
            (d["card_id"], d["billing_start"], d["billing_end"])
        ).fetchall()
        keep = rows[0]
        donor = next((r for r in rows[1:] if r["due_date"]), None)
        if donor and not keep["due_date"]:
            conn.execute("UPDATE statements SET due_date=?, due_date_source=? WHERE id=?",
                         (donor["due_date"], donor["due_date_source"], keep["id"]))
        donor = next((r for r in rows[1:] if r["min_due"] is not None), None)
        if donor and keep["min_due"] is None:
            conn.execute("UPDATE statements SET min_due=? WHERE id=?", (donor["min_due"], keep["id"]))
        for extra in rows[1:]:
            conn.execute("DELETE FROM statements WHERE id=?", (extra["id"],))
            removed += 1
    if removed:
        conn.commit()
    return removed


RECONCILE_MIN_GAP_RS = 10.0


def reconcile_statements_logic(threshold_pct=1.0, conn=None):
    """Does each statement add up? Flag needs_review only for a gap nothing explains — the honest
    signal that lines are missing. (iter-16 C1; like with like since iteration 203 E12)

    The bank's Total Amount Due is a BALANCE (last bill's leftover, payments, EMI instalments billed
    from an EMI loan), while computed_total is one date window's charges minus credits; comparing the
    two flagged 41 statements, nearly all of them carry-overs and EMI bookings, and the window misses
    lines dated on the previous close day that the bank billed on this statement. So, best first:

      printed-summary  the statement PDF printed its account summary: its purchases must equal the
                       debit lines it printed (SBI bills EMI instalments outside "purchases": a gap of
                       exactly the EMI principal lines is explained) and its payments & credits must
                       equal its credit + payment lines. 121 of 124 PDFs add up to the paisa.
      balance          no PDF summary, previous statement known: stated = previous stated total -
                       payments in this cycle + this cycle's net charges.
      cycle            first statement we have: stated vs the cycle's net charges; a stated total ABOVE
                       the charges may be a balance we never saw, so only a shortfall is flagged.

    A gap counts when it exceeds max(Rs 10, threshold_pct of the bigger figure). A statement with ZERO
    captured lines is not flagged (periods before our history). computed_total keeps its meaning (the
    window's net charges, the amount due when no stated total exists)."""
    conn = conn or get_db()
    _dedup_statement_windows(conn)
    stmts = conn.execute(
        "SELECT s.id, s.card_id, s.stated_total, s.billing_start, s.billing_end, s.printed_purchases, "
        "s.printed_credits, s.lines_charges, s.lines_credits, s.lines_emi_principal, c.last4 "
        "FROM statements s JOIN cards c ON c.id = s.card_id "
        "WHERE s.billing_start IS NOT NULL AND s.billing_end IS NOT NULL "
        "ORDER BY s.card_id, s.billing_end"
    ).fetchall()
    flagged = reconciled = 0
    by_method = defaultdict(lambda: {"checked": 0, "flagged": 0})
    previous = {}                                    # card_id -> previous statement row
    for s in stmts:
        computed = _net_card_total(conn, s["last4"], s["billing_start"], s["billing_end"])
        stated = s["stated_total"]
        method, gap, scale = _reconcile_gap(conn, s, computed, previous.get(s["card_id"]))
        previous[s["card_id"]] = s
        needs, variance = 0, None
        if method == "printed-summary" or (stated and stated > 0):
            limit = max(RECONCILE_MIN_GAP_RS, threshold_pct / 100.0 * abs(scale))
            variance = round(abs(gap) / abs(scale) * 100, 2) if scale else None
            unexplained = abs(gap) > limit and not (method == "cycle" and gap > 0)
            needs = 1 if unexplained and (computed != 0 or method == "printed-summary") else 0
        flagged += needs
        by_method[method]["checked"] += 1
        by_method[method]["flagged"] += needs
        conn.execute(
            "UPDATE statements SET computed_total=?, variance_pct=?, needs_review=?, reconcile_gap=?, "
            "reconcile_method=? WHERE id=?",
            (computed, variance, needs, round(gap, 2), method, s["id"])
        )
        reconciled += 1
    conn.commit()
    return {"reconciled": reconciled, "needs_review": flagged, "by_method": dict(by_method)}


def _reconcile_gap(conn, s, computed, prev):
    """(method, gap, scale) for one statement (see reconcile_statements_logic)."""
    if s["printed_purchases"] is not None and s["lines_charges"] is not None:
        charges_gap = round(s["printed_purchases"] - s["lines_charges"], 2)
        emi = s["lines_emi_principal"] or 0.0
        if emi and abs(charges_gap + emi) <= 1.0:
            charges_gap = 0.0                        # EMI instalments billed outside "purchases"
        credits_gap = round((s["printed_credits"] or 0.0) - (s["lines_credits"] or 0.0), 2)
        gap = charges_gap if abs(charges_gap) >= abs(credits_gap) else credits_gap
        return "printed-summary", gap, max(abs(s["printed_purchases"]), abs(s["printed_credits"] or 0.0), 1.0)
    stated = s["stated_total"] or 0.0
    if prev is not None and prev["stated_total"] is not None:
        group = _group_last4s(conn, s["last4"])
        paid = conn.execute(
            f"SELECT COALESCE(SUM(amount), 0) AS p FROM cc_payments WHERE card_last4 IN ({','.join('?' * len(group))}) "
            "AND payment_date > ? AND payment_date <= ?", (*group, prev["billing_end"], s["billing_end"])
        ).fetchone()["p"]
        expected = prev["stated_total"] - paid + computed
        return "balance", round(stated - expected, 2), stated
    return "cycle", round(stated - computed, 2), stated


def record_statement_summary(conn, card_last4, period_end_iso, summary):
    """Attach a statement PDF's printed account summary and the sums of its own lines to the card's
    statement row closing within 3 days of the PDF's period end (E12). Returns the row id or None."""
    card = _resolve_card(conn, card_last4) or _resolve_card(conn, card_last4, active_only=False)
    if not card or not period_end_iso:
        return None
    row = conn.execute(
        "SELECT id FROM statements WHERE card_id=? AND billing_end IS NOT NULL "
        "AND ABS(julianday(billing_end) - julianday(?)) <= 3 "
        "ORDER BY ABS(julianday(billing_end) - julianday(?)), id LIMIT 1",
        (card["id"], period_end_iso, period_end_iso)
    ).fetchone()
    if not row:
        return None
    cols = ("printed_previous", "printed_purchases", "printed_credits", "lines_charges", "lines_credits",
            "lines_emi_principal")
    conn.execute(f"UPDATE statements SET {', '.join(c + '=?' for c in cols)} WHERE id=?",
                 (*(summary.get(c) for c in cols), row["id"]))
    return row["id"]


def _net_card_total(conn, last4, billing_start, billing_end):
    """Charges minus credits on the statement lines of one cycle (the NET a bill is computed from).

    Bill payments are not part of what a statement charges (a payment line relabelled by the
    iter-18 PDF fixes must not shrink the bill), and statement lines filed under the card's PDF
    alias (SBI prints 9181/8002 for 2111/2971) belong to the card."""
    group = _group_last4s(conn, last4)          # an add-on's lines are on the primary's bill
    aliases = [r["pdf_card_last4"] for r in conn.execute(
        f"SELECT pdf_card_last4 FROM cards WHERE last4 IN ({','.join('?' * len(group))}) "
        "AND pdf_card_last4 IS NOT NULL", group)]
    numbers = sorted(set(group) | set(aliases))
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN txn_type='credit' THEN -amount ELSE amount END),0) AS total "
        "FROM transactions WHERE source='card' AND is_duplicate=0 "
        "AND COALESCE(kind, '') <> 'bill_payment' "
        f"AND card_last4 IN ({','.join('?' * len(numbers))}) "
        "AND date >= ? AND date <= ?",
        (*numbers, billing_start, billing_end)
    ).fetchone()
    return round(row["total"] or 0, 2)


SBI_BBPS_NO_CARD = re.compile(
    r"received payment of Rs\.?\s*([\d,]+\.?\d*).*?BBPS.*?SBI Credit Card(?!\s*(?:ending|xxxx|XX))",
    re.IGNORECASE | re.DOTALL)


def attribute_bbps_payments_logic(conn=None):
    """Attribute SBI BBPS payments that name no card to the right SBI card. (iter-16 C4/C5)

    SBI's BBPS confirmation says 'credited to your SBI Credit Card' with no last-4, so
    these payments could never be pinned to Cashback (2111) vs AURUM (2971). But people
    pay a statement in full, so the payment AMOUNT usually equals a card's authoritative
    stated_total. Match each unattributed BBPS payment to the SBI card whose recent
    statement stated_total equals it (within Rs.1); attribute only on a UNIQUE match so a
    coincidental tie is never guessed. Idempotent via the same +/-2-day dedup as the SMS
    backfill. Depends on C1 having populated stated_total.

    iter-18 M1: writes through record_card_payment, dates the payment by its IST arrival day, and
    finishes by attributing CRED debits on the owner's bank accounts (attribute_bank_debits_logic).
    """
    conn = conn or get_db()
    sbi_cards = [r["last4"] for r in conn.execute(
        "SELECT last4 FROM cards WHERE bank='SBI' AND active=1"
    ).fetchall()]
    attributed = ambiguous = unmatched = reattributed = 0
    rows = []
    if sbi_cards:
        rows = conn.execute(
            "SELECT id, body, phone_timestamp, received_at FROM sms WHERE "
            "body LIKE '%received payment of%' AND body LIKE '%BBPS%' AND body LIKE '%SBI Credit Card%'"
        ).fetchall()

    for sms in rows:
        m = SBI_BBPS_NO_CARD.search(sms["body"])
        if not m:
            continue
        amount = float(m.group(1).replace(",", ""))
        pay_date = ist_date_of(sms["phone_timestamp"] or sms["received_at"])
        if not pay_date:
            continue
        # candidate cards whose stated statement total equals this payment (within Rs.1),
        # for a statement due on/around the payment (within ~45 days before)
        matches = conn.execute(
            "SELECT DISTINCT c.last4 FROM statements s JOIN cards c ON c.id=s.card_id "
            "WHERE c.bank='SBI' AND c.active=1 AND s.stated_total IS NOT NULL "
            "AND ABS(s.stated_total - ?) <= 1.0 "
            "AND julianday(?) - julianday(s.billing_end) BETWEEN 0 AND 45",
            (amount, pay_date)
        ).fetchall()
        if len(matches) != 1:
            if len(matches) > 1:
                ambiguous += 1
            else:
                unmatched += 1
            continue
        card_last4 = matches[0]["last4"]
        # A row written for this SMS by the retired available-limit heuristic (source='sms')
        # is corrected in place when a unique stated-total match names a different card.
        # (iter-17 P4)
        prior = conn.execute(
            "SELECT id, card_last4 FROM cc_payments WHERE sms_id=? AND source='sms'", (sms["id"],)
        ).fetchone()
        if prior:
            if prior["card_last4"] != card_last4:
                dup = conn.execute(
                    "SELECT id FROM cc_payments WHERE card_last4=? AND amount=? AND id<>? "
                    "AND ABS(julianday(payment_date) - julianday(?)) <= 2",
                    (card_last4, amount, prior["id"], pay_date)
                ).fetchone()
                if dup:  # target card already has this payment: drop the mis-attributed copy
                    conn.execute("DELETE FROM cc_payments WHERE id=?", (prior["id"],))
                else:
                    conn.execute(
                        "UPDATE cc_payments SET card_last4=?, source='sms-bbps-attributed' WHERE id=?",
                        (card_last4, prior["id"])
                    )
                reattributed += 1
            continue
        res = record_card_payment(conn, card_last4, amount, pay_date, "sms-bbps-attributed",
                                  sms_id=sms["id"], raw_text=sms["body"])
        attributed += res["status"] == "inserted"

    conn.commit()
    return {"attributed": attributed, "reattributed": reattributed,
            "ambiguous": ambiguous, "unmatched": unmatched,
            "bank_debits": attribute_bank_debits_logic(conn)}


def attribute_bank_debits_logic(conn=None, window_days=45, tolerance=1.0):
    """iter-18 M1: a card bill paid through CRED from the owner's bank account shows up as a debit
    to CRED (category Finance) on that account — often with no bank "payment received" SMS for the
    card. Attribute such a debit to a card when it equals what was still owed on exactly ONE
    statement, or on exactly ONE pair of statements of two different cards (one CRED checkout
    paying two bills: the Sep-04 HSBC debit of Rs 87,741.63 = 0026's remaining Rs 58,766.63 +
    2111's Rs 28,975), within +/-Rs 1, for statements that closed at most `window_days` before the
    debit. Anything ambiguous is left alone. "Still owed" is the amount due minus payments made
    more than two days before the debit, so a part-payment earlier in the cycle is accounted for
    and this debit's own (possibly already recorded) payment never hides its statement. An
    attributed payment yields to a direct confirmation of it (_confirmed_payment_near)."""
    conn = conn or get_db()
    superseded = _drop_superseded_debit_payments(conn)
    card_rows = conn.execute(f"SELECT {_CARD_COLS}, pdf_card_last4 FROM cards WHERE active=1").fetchall()
    card_numbers = {r["last4"] for r in card_rows} | {r["pdf_card_last4"] for r in card_rows if r["pdf_card_last4"]}
    primaries = {}
    for r in card_rows:
        p = _billing_primary(conn, r)
        primaries[p["id"]] = p
    debits = conn.execute(
        "SELECT id, date, amount, card_last4 FROM transactions WHERE txn_type='debit' AND is_duplicate=0 "
        "AND COALESCE(currency, 'INR')='INR' "
        "AND (category='Finance' OR UPPER(TRIM(COALESCE(merchant, '')))='CRED') ORDER BY date, id"
    ).fetchall()
    attributed = ambiguous = unmatched = considered = confirmed = 0
    for debit in debits:
        if (debit["card_last4"] or "") in card_numbers:
            continue  # spent ON a card (e.g. rent via CRED), not a payment TO one
        considered += 1
        on = (debit["date"] or "")[:10]
        cands = []
        for card in primaries.values():
            group = _group_last4s(conn, card["last4"])
            marks = ",".join("?" * len(group))
            for s in conn.execute(
                "SELECT id, billing_start, billing_end, stated_total, computed_total FROM statements "
                "WHERE card_id=? AND billing_end IS NOT NULL AND julianday(?) - julianday(billing_end) BETWEEN 0 AND ?",
                (card["id"], on, window_days)
            ).fetchall():
                due = statement_amount_due(conn, card["last4"], s)
                paid = conn.execute(
                    f"SELECT COALESCE(SUM(amount), 0) AS p FROM cc_payments WHERE card_last4 IN ({marks}) "
                    "AND payment_date > ? AND julianday(payment_date) < julianday(?) - 2",
                    (*group, s["billing_end"], on)
                ).fetchone()["p"]
                owed = round(due - paid, 2)
                if owed > max(1.0, 0.005 * due):
                    cands.append({"card": card["last4"], "owed": owed})
        amount = float(debit["amount"])
        singles = [c for c in cands if abs(c["owed"] - amount) <= tolerance]
        pairs = [(a, b) for a, b in combinations(cands, 2)
                 if a["card"] != b["card"] and abs(a["owed"] + b["owed"] - amount) <= tolerance]
        if len(singles) + len(pairs) != 1:
            if singles or pairs:
                ambiguous += 1
            else:
                unmatched += 1
            continue
        ref = f"txn:{debit['id']}"
        parts = [(singles[0]["card"], amount)] if singles else [(c["card"], c["owed"]) for c in pairs[0]]
        for card_last4, paid_amount in parts:
            if _confirmed_payment_near(conn, card_last4, paid_amount, on):
                confirmed += 1
                continue
            res = record_card_payment(conn, card_last4, paid_amount, on, "bank-debit-attributed", ref=ref)
            attributed += res["status"] == "inserted"
    conn.commit()
    return {"debits": considered, "attributed": attributed, "already_confirmed": confirmed,
            "superseded_removed": superseded, "ambiguous": ambiguous, "unmatched": unmatched}


# The money leaves the bank account first; the card is credited — and the bank's "payment
# received" SMS sent — up to a few days later (HSBC debit to CRED on 2026-03-12, 8938's
# confirmation on 03-16). Within this window a direct confirmation is the same payment.
_DEBIT_TO_CREDIT_DAYS = (-2, 7)


def _confirmed_payment_near(conn, card_last4, amount, debit_date):
    """A payment for this card (or its billing group) of this amount, reported by any channel
    other than debit attribution, dated from 2 days before to 7 days after the bank debit."""
    group = _group_last4s(conn, card_last4)
    marks = ",".join("?" * len(group))
    lo, hi = _DEBIT_TO_CREDIT_DAYS
    return conn.execute(
        f"SELECT 1 FROM cc_payments WHERE card_last4 IN ({marks}) AND ABS(amount - ?) < 0.005 "
        "AND source <> 'bank-debit-attributed' "
        "AND julianday(payment_date) - julianday(?) BETWEEN ? AND ? LIMIT 1",
        (*group, round(float(amount), 2), debit_date, lo, hi)
    ).fetchone() is not None


def _drop_superseded_debit_payments(conn):
    """A debit-attributed payment is inferred evidence. Once the bank or CRED confirms the same
    payment directly (possibly days later, outside record_card_payment's +/-2-day identity), the
    inferred row is removed so the payment is not counted twice."""
    removed = 0
    for p in conn.execute(
        "SELECT id, card_last4, amount, payment_date FROM cc_payments WHERE source='bank-debit-attributed'"
    ).fetchall():
        if _confirmed_payment_near(conn, p["card_last4"], p["amount"], p["payment_date"]):
            conn.execute("DELETE FROM cc_payments WHERE id=?", (p["id"],))
            removed += 1
    return removed


def backfill_card_ids_logic(conn=None):
    """Populate transactions.card_id from card_last4 for active cards. (iter-16 C2)

    Normalizes PDF-derived last4s (e.g. SBI PDF '9181' -> card 2111) via cards.pdf_card_last4.
    Deliberately does NOT touch rows whose last4 belongs to a DEACTIVATED card — notably
    HDFC 6611, which the owner confirmed is a bank account (UPI), not a card, so its spends
    stay card-less. Bank-account and voucher rows (HSBC A/c, Pluxee) have no last4 and are
    untouched.
    """
    conn = conn or get_db()
    # direct match to an active card
    direct = conn.execute(
        "UPDATE transactions SET card_id = (SELECT id FROM cards WHERE cards.last4 = transactions.card_last4 AND cards.active=1) "
        "WHERE card_id IS NULL AND card_last4 IS NOT NULL AND card_last4 <> '' "
        "AND EXISTS (SELECT 1 FROM cards WHERE cards.last4 = transactions.card_last4 AND cards.active=1)"
    ).rowcount
    # PDF-derived last4 -> real card via pdf_card_last4
    pdf = conn.execute(
        "UPDATE transactions SET card_id = (SELECT id FROM cards WHERE cards.pdf_card_last4 = transactions.card_last4 AND cards.active=1) "
        "WHERE card_id IS NULL AND card_last4 IS NOT NULL AND card_last4 <> '' "
        "AND EXISTS (SELECT 1 FROM cards WHERE cards.pdf_card_last4 = transactions.card_last4 AND cards.active=1)"
    ).rowcount
    conn.commit()
    remaining = conn.execute(
        "SELECT COUNT(*) c FROM transactions WHERE card_id IS NULL AND card_last4 IS NOT NULL AND card_last4 <> ''"
    ).fetchone()["c"]
    return {"card_id_backfilled_direct": direct, "backfilled_via_pdf_alias": pdf, "still_unattributed": remaining}


def dedup_payments_logic(conn=None, dry_run=False):
    """Remove duplicate payments (same card, same amount, dates within SAME_PAYMENT_DAYS) — unless
    their evidence says they are two payments (distinct references, statement lines or bank SMS of the
    same wording: iteration 203 E8). The kept row takes the removed row's evidence it lacks.

    The window is record_card_payment's own (+/-2 days): with only +/-1 day here, the bank's two
    wordings of one payment recorded two days apart by an older build stayed two payments
    (verifier round 2: three July-2025 payments). Returns {deleted, merged: [{kept, removed, ...}],
    total_checked, dry_run}; a dry run writes nothing."""
    conn = conn or get_db()
    payments = conn.execute(
        "SELECT * FROM cc_payments ORDER BY card_last4, amount, payment_date, id"
    ).fetchall()
    merged = []
    prev = None
    for p in payments:
        if prev and prev["card_last4"] == p["card_last4"] and prev["amount"] == p["amount"]:
            try:
                d1 = datetime.strptime(prev["payment_date"], "%Y-%m-%d")
                d2 = datetime.strptime(p["payment_date"], "%Y-%m-%d")
                if (abs((d2 - d1).days) <= SAME_PAYMENT_DAYS
                        and not _reports_differ(conn, prev, p["sms_id"], p["ref"], p["stmt_ref"])):
                    merged.append({"kept": prev["id"], "removed": p["id"], "card_last4": p["card_last4"],
                                   "amount": p["amount"], "dates": [prev["payment_date"], p["payment_date"]]})
                    continue
            except Exception:
                pass
        prev = p

    if not dry_run:
        for m in merged:
            gone = conn.execute("SELECT sms_id, email_id, ref, stmt_ref FROM cc_payments WHERE id = ?",
                                (m["removed"],)).fetchone()
            conn.execute(
                "UPDATE cc_payments SET sms_id = COALESCE(sms_id, ?), email_id = COALESCE(email_id, ?), "
                "ref = COALESCE(ref, ?), stmt_ref = COALESCE(stmt_ref, ?) WHERE id = ?",
                (gone["sms_id"], gone["email_id"], gone["ref"], gone["stmt_ref"], m["kept"]))
            conn.execute("DELETE FROM cc_payments WHERE id = ?", (m["removed"],))
        conn.commit()
    return {"deleted": len(merged), "merged": merged, "total_checked": len(payments), "dry_run": dry_run}


# --- Bill status (iter-18 H1 / H2 / M11) ------------------------------------------------------------

PAYMENT_STATUSES = ("paid", "partial", "unpaid", "overdue", "not_confirmed", "no_dues")
CURRENT_CYCLE_DAYS = 45
# Spends this close to a statement's close that matched no statement line are still unbilled.
UNBILLED_TAIL_DAYS = 3


def statement_amount_due(conn, card_last4, stmt):
    """What the bill asks for: the bank's stated total when known, else the NET of the cycle's
    statement lines (charges minus refunds/cashback, as reconcile computes it) — never gross
    debits. 9434's Sep-26 cycle is Rs 5,110 of debits and Rs 511 of cashback; the bill was
    Rs 4,599, and paying that is paying in full. (iter-18 H2)"""
    if stmt["stated_total"] is not None:
        return round(stmt["stated_total"], 2)
    if stmt["computed_total"] is not None:
        return round(stmt["computed_total"], 2)
    if stmt["billing_start"] and stmt["billing_end"]:
        return _net_card_total(conn, card_last4, stmt["billing_start"], stmt["billing_end"])
    return 0.0


def estimated_due_date(billing_end, due_day):
    """Configured-day estimate for a statement whose bank due date we never saw: the first
    `due_day` after the close (clamped to the month's length)."""
    be = date.fromisoformat(billing_end)
    y, m = (be.year, be.month) if due_day > be.day else ((be.year + 1, 1) if be.month == 12 else (be.year, be.month + 1))
    return date(y, m, min(due_day, calendar.monthrange(y, m)[1])).isoformat()


def payment_status(amount_due, paid_since_close, due_date, billing_end, today, credits=0.0):
    """(status, remaining_due) for one statement, decided on IST calendar days. (iter-18 M11)

    paid          payments + credits since close >= amount due - max(Rs 1, 0.5%)
    no_dues       amount due <= 0 (nothing billed, or a credit balance)
    partial       something paid, not all, due date not passed
    unpaid        nothing paid, due date not passed
    overdue       IST today is past the due date and more than the tolerance is still owed
    not_confirmed the due date passed with no payment evidence at all and the statement is more
                  than 45 days old — we cannot tell; replaces the lifetime-ratio guess that
                  showed 2971 "paid" with no payment since February

    `credits` (iteration 203 E8): refunds / reversals / cashback that reached the card after the
    statement closed and by its due date. The bank applies them to the balance, so they reduce what is
    still owed — a Rs 613 refund the day after the close left the Swiggy card "overdue Rs 613" for ten
    days although the owner had paid the rest. A credit alone is not a payment ('unpaid', not 'partial').
    """
    amount_due = round(amount_due or 0.0, 2)
    paid_since_close = round(paid_since_close or 0.0, 2)
    settled = round(paid_since_close + (credits or 0.0), 2)
    remaining = round(max(amount_due - settled, 0.0), 2)
    if amount_due <= 0:
        return "no_dues", 0.0
    if settled >= amount_due - max(1.0, 0.005 * amount_due):
        return "paid", remaining
    if due_date and today > date.fromisoformat(due_date):
        if paid_since_close <= 0 and (today - date.fromisoformat(billing_end)).days > CURRENT_CYCLE_DAYS:
            return "not_confirmed", remaining
        return "overdue", remaining
    return ("partial" if paid_since_close > 0 else "unpaid"), remaining


def _statement_due_date(stmt, due_day):
    """(due_date, due_date_source per the /cards contract, origin) for a statement row."""
    if stmt["due_date"]:
        # CRED relays the issuer's own date; the contract only distinguishes bank vs estimate.
        return stmt["due_date"], "bank", stmt["due_date_source"] or "bank"
    if stmt["billing_end"]:
        return estimated_due_date(stmt["billing_end"], due_day), "estimated", "estimated"
    return None, "estimated", "estimated"


def statement_view(conn, card, stmt):
    """One statement as the API shows it (summary's latest_statement and /cards/{id}/statements)."""
    due_day = card["due_day"] or 20
    due_date, due_src, origin = _statement_due_date(stmt, due_day)
    return {
        "id": stmt["id"], "month": stmt["month"], "year": stmt["year"],
        "total": stmt["total_amount"], "txn_count": stmt["transaction_count"],
        "billing_start": stmt["billing_start"], "billing_end": stmt["billing_end"],
        "due_date": due_date, "due_date_source": due_src, "due_date_origin": origin,
        "amount_due": statement_amount_due(conn, card["last4"], stmt),
        "stated_total": stmt["stated_total"], "computed_total": stmt["computed_total"],
        "min_due": stmt["min_due"], "total_source": stmt["total_source"],
    }


_STMT_COLS = ("id, month, year, total_amount, stated_total, computed_total, total_source, needs_review, "
              "variance_pct, transaction_count, billing_start, billing_end, status, due_date, "
              "due_date_source, min_due")


def statement_rows(conn, card_id):
    """/cards/{id}/statements: every statement, newest close first, with the bill fields."""
    card = conn.execute("SELECT id, last4, due_day FROM cards WHERE id=?", (card_id,)).fetchone()
    if not card:
        return []
    out = []
    for r in conn.execute(
        f"SELECT {_STMT_COLS} FROM statements WHERE card_id=? "
        "ORDER BY (billing_end IS NULL), billing_end DESC, year DESC, month DESC, id DESC", (card_id,)
    ).fetchall():
        row = dict(r)
        view = statement_view(conn, card, r)
        row.update({k: view[k] for k in ("due_date", "due_date_source", "due_date_origin", "amount_due")})
        out.append(row)
    return out


def latest_statement_row(conn, card_id, today):
    """The most recently CLOSED statement, by its close date — not by its (month, year) label,
    which follows two conventions (stmt_day==1 cards are labelled by spending month). (iter-18 H2)"""
    return conn.execute(
        f"SELECT {_STMT_COLS} FROM statements WHERE card_id=? AND (billing_end IS NULL OR billing_end <= ?) "
        "ORDER BY (billing_end IS NULL), billing_end DESC, year DESC, month DESC, id DESC LIMIT 1",
        (card_id, today.isoformat())
    ).fetchone()


CREDIT_KINDS_TOWARD_BILL = ("refund", "reversal", "cashback")


def _card_numbers(conn, group):
    """A billing group's last-4s plus the statement aliases its lines may carry (an SBI card's PDF prints another number)."""
    aliases = [r["pdf_card_last4"] for r in conn.execute(
        f"SELECT pdf_card_last4 FROM cards WHERE last4 IN ({','.join('?' * len(group))}) "
        "AND pdf_card_last4 IS NOT NULL", group)]
    return sorted(set(group) | set(aliases))


def _credits_since_close(conn, group, billing_end, due_date, today):
    """Refunds / reversals / cashback that reached the card after the statement closed and no later
    than its due date (or today): the bank applies them to the balance. (iteration 203 E8)

    A credit dated ON the close day counts too when a copy of it (the bank's SMS / e-mail) is dated
    after the close: the bank billed it on the next statement (verifier round 2: the Rs 613 refund
    whose statement line is dated on the July close day; its SMS came the next day). With nothing
    dated after the close, the statement's own total already holds it."""
    if not billing_end:
        return 0.0
    until = min(due_date or today.isoformat(), today.isoformat())
    numbers = _card_numbers(conn, group)
    row = conn.execute(
        "SELECT COALESCE(SUM(t.amount), 0) AS total FROM transactions t WHERE t.is_duplicate=0 "
        f"AND t.txn_type='credit' AND t.card_last4 IN ({','.join('?' * len(numbers))}) "
        "AND t.date >= ? AND t.date <= ? "
        f"AND COALESCE(t.kind, '') IN ({','.join('?' * len(CREDIT_KINDS_TOWARD_BILL))}) "
        "AND (t.date > ? OR EXISTS (SELECT 1 FROM transactions c WHERE c.matched_txn_id = t.id "
        "AND c.is_duplicate = 1 AND c.date > ?))",
        (*numbers, billing_end, until, *CREDIT_KINDS_TOWARD_BILL, billing_end, billing_end)
    ).fetchone()
    return round(row["total"] or 0.0, 2)


def _previous_bill(conn, card, stmt, group, payments_after_close):
    """The previous statement when it was still unpaid at this statement's close and this statement's
    amount does not already carry it — then payments after the close settle the OLDER bill first
    (iteration 203 E8: a late Rs 10,000 payment of last month's bill marked a new Rs 5,000 bill 'paid').

    A bank's Total Amount Due is the whole balance, unpaid earlier bills included, so a statement with a
    stated total needs no carry. A previous bill with no payment at all in its cycle is carried only when
    a later payment is exactly what it still owed: a payment we never saw must not make every new
    payment disappear into an old bill. Returns None or {billing_end, amount_due, paid_in_cycle,
    paid_after_close, remaining}."""
    if stmt["stated_total"] is not None or not stmt["billing_end"]:
        return None
    prev = conn.execute(
        f"SELECT {_STMT_COLS} FROM statements WHERE card_id=? AND billing_end IS NOT NULL AND billing_end < ? "
        "ORDER BY billing_end DESC LIMIT 1", (card["id"], stmt["billing_end"])
    ).fetchone()
    if not prev:
        return None
    prev_due = statement_amount_due(conn, card["last4"], prev)
    if prev_due <= 0:
        return None
    marks = ",".join("?" * len(group))
    paid_in_cycle = round(conn.execute(
        f"SELECT COALESCE(SUM(amount), 0) AS p FROM cc_payments WHERE card_last4 IN ({marks}) "
        "AND payment_date > ? AND payment_date <= ?", (*group, prev["billing_end"], stmt["billing_end"])
    ).fetchone()["p"], 2)
    owed = round(prev_due - paid_in_cycle, 2)
    tolerance = max(1.0, 0.005 * prev_due)
    if owed <= tolerance:
        return None
    if paid_in_cycle <= 0 and not any(abs(p["amount"] - owed) <= tolerance for p in payments_after_close):
        return None
    allocated = 0.0
    for p in payments_after_close:                 # chronological
        if allocated >= owed - tolerance:
            break
        allocated += min(p["amount"], owed - allocated)
    return {"billing_end": prev["billing_end"], "amount_due": prev_due, "paid_in_cycle": paid_in_cycle,
            "paid_after_close": round(allocated, 2), "remaining": round(max(owed - allocated, 0.0), 2)}


def unbilled_tail_start(conn, last4, billing_end):
    """Start of the unbilled 'tail' of the cycle that closed on `billing_end` ('' when there is none):
    once a cycle's statement lines are parsed, an SMS/email spend from its last UNBILLED_TAIL_DAYS
    days that matched no statement line was not billed. Used by the summary's Unbilled tile and by
    /cards/{id}/transactions?period=unbilled, so the two always agree (iteration 203 E2 M7)."""
    if not billing_end:
        return ""
    has_lines = conn.execute(
        "SELECT 1 FROM transactions WHERE source='card' AND card_last4=? AND is_duplicate=0 "
        "AND date > date(?, '-35 day') AND date <= ? LIMIT 1",
        (last4, billing_end, billing_end)
    ).fetchone()
    if not has_lines:
        return ""
    return (date.fromisoformat(billing_end) - timedelta(days=UNBILLED_TAIL_DAYS)).isoformat()


def build_cards_summary(conn=None, today=None):
    """/cards/summary. Every pre-iter-18 field is kept; added per card: latest_statement.{due_date
    (bank's when known), due_date_source ('bank'|'estimated'), amount_due, stated_total,
    computed_total, min_due}, payment_status, remaining_due, current_cycle, addon_of (a card billed
    on another card's statement) and unbilled.credits. `today` is the IST date."""
    conn = conn or get_db()
    load_pdf_aliases(conn)
    today = today or today_ist()
    cards = conn.execute(
        "SELECT id, bank, card_name, last4, network, billing_group, "
        "statement_day, due_day, pdf_card_last4, replaced_by_card_id FROM cards WHERE active=1"
    ).fetchall()
    emi_since = (today - timedelta(days=60)).isoformat()
    result = []
    for card in cards:
        card_data = dict(card)
        last4 = card["last4"]
        primary = _billing_primary(conn, conn.execute(f"SELECT {_CARD_COLS} FROM cards WHERE id=?", (card["id"],)).fetchone())
        if primary["id"] != card["id"]:
            card_data["addon_of"] = primary["last4"]
        stmt = latest_statement_row(conn, card["id"], today)

        if stmt:
            view = statement_view(conn, card, stmt)
            card_data["latest_statement"] = view
            billing_end = stmt["billing_end"] or ""
            group = _group_last4s(conn, last4)
            marks = ",".join("?" * len(group))
            # Payments made after the statement closed are what pays it (a payment on the close
            # date itself is already inside that statement).
            payments = conn.execute(
                f"SELECT amount, payment_date, source FROM cc_payments WHERE card_last4 IN ({marks}) "
                "AND payment_date > ? ORDER BY payment_date ASC",
                (*group, billing_end)
            ).fetchall()
            total_paid = round(sum(p["amount"] for p in payments), 2)
            # iteration 203 E8: an unpaid older bill takes the payments first; credits after the
            # close count toward this bill
            previous = _previous_bill(conn, card, stmt, group, payments)
            toward_this_bill = round(total_paid - (previous["paid_after_close"] if previous else 0.0), 2)
            credits = _credits_since_close(conn, group, billing_end, view["due_date"], today)
            status, remaining = payment_status(view["amount_due"], toward_this_bill, view["due_date"],
                                               billing_end or today.isoformat(), today, credits=credits)
            card_data["payment_status"] = status
            card_data["remaining_due"] = remaining
            card_data["credits_since_close"] = credits
            card_data["previous_bill"] = previous
            card_data["current_cycle"] = bool(billing_end) and \
                0 <= (today - date.fromisoformat(billing_end)).days <= CURRENT_CYCLE_DAYS
            card_data["is_paid"] = status in ("paid", "no_dues")
            card_data["payment"] = {
                "total": total_paid, "toward_this_bill": toward_this_bill, "count": len(payments),
                "latest_date": payments[-1]["payment_date"] if payments else None,
                "payments": [{"amount": p["amount"], "date": p["payment_date"], "source": p["source"]}
                             for p in payments],
            } if payments else None
        else:
            card_data["latest_statement"] = None
            card_data["payment_status"] = "no_dues"
            card_data["remaining_due"] = 0.0
            card_data["credits_since_close"] = 0.0
            card_data["previous_bill"] = None
            card_data["current_cycle"] = False
            card_data["is_paid"] = True
            card_data["payment"] = None
            billing_end = ""
            if primary["id"] != card["id"]:
                # An add-on is billed on its primary's statement: its unbilled spend runs from the
                # primary's last close, not from the beginning of time (8013 on 1019's bill).
                primary_stmt = latest_statement_row(conn, primary["id"], today)
                billing_end = (primary_stmt["billing_end"] or "") if primary_stmt else ""

        # Unbilled = what has happened since the statement closed (everything, for a card that has
        # never had a statement), from every channel: SMS, statement lines and — iter-18 — bank
        # alert emails (SBI/ICICI email-only spends). Duplicates across channels are linked by the
        # merge rules, so is_duplicate=0 counts each purchase once. Refunds/cashback are shown
        # separately rather than silently netted.
        # A spend dated on (or just before) the close that the bank did not put on that statement is
        # still unbilled: ICICI 0026's statement for Aug 21 - Sep 20 ends its lines on 09-19, and the
        # 09-20/09-21 spends are on the NEXT bill (CRED counts them; the date-only rule dropped them,
        # iter-18 verifier). Once a cycle's statement lines are parsed, an SMS/email row from the last
        # days of the window that matched no statement line (is_duplicate=0) was not billed.
        tail_start = unbilled_tail_start(conn, last4, billing_end)
        unbilled = {}
        for kind in ("debit", "credit"):
            row = conn.execute(
                "SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total FROM transactions "
                "WHERE source IN ('card','sms','email') AND card_last4=? AND txn_type=? AND is_duplicate=0 "
                "AND (date > ? OR (? <> '' AND source IN ('sms','email') AND date > ? AND date <= ?))",
                (last4, kind, billing_end, tail_start, tail_start, billing_end)
            ).fetchone()
            unbilled[kind] = {"total": round(row["total"], 2), "count": row["count"]}
        card_data["unbilled"] = {**unbilled["debit"], "credits": unbilled["credit"]}

        # Detect active EMIs (EMI transactions in last 60 IST days)
        emi_patterns = ["%%OFFUS EMI%%", "%%Principal Amount%%", "%%PRINCIPAL DEBIT%%", "%%Pay in EMIs%%", "EMI %%"]
        emi_series = conn.execute(
            "SELECT COUNT(DISTINCT COALESCE(merchant, raw_description)) as cnt "
            "FROM transactions WHERE source IN ('card','sms','email') AND card_last4=? "
            "AND is_duplicate=0 AND date >= ? "
            "AND (raw_description LIKE ? OR raw_description LIKE ? "
            "OR raw_description LIKE ? OR raw_description LIKE ? "
            "OR raw_description LIKE ?)",
            (last4, emi_since, *emi_patterns)
        ).fetchone()
        card_data["active_emis"] = emi_series["cnt"] if emi_series else 0

        stats = conn.execute(
            "SELECT COUNT(*) as count, COALESCE(SUM(CASE WHEN txn_type='debit' THEN amount ELSE 0 END), 0) as total "
            "FROM transactions WHERE source IN ('card','sms','email') AND card_last4=? AND is_duplicate=0",
            (last4,)
        ).fetchone()
        card_data["total_spend"] = round(stats["total"], 2) if stats else 0
        card_data["total_txns"] = stats["count"] if stats else 0
        result.append(card_data)
    return {"cards": result}


# --- Card configuration (iter-18 M10) ---------------------------------------------------------------

class CardConfigError(ValueError):
    """Invalid card configuration input (HTTP 400)."""


class CardConflict(ValueError):
    """The card already exists (HTTP 409)."""


CARD_UPDATABLE = ("card_name", "statement_day", "due_day", "pdf_card_last4", "active", "replaced_by_card_id")
CARD_CREATABLE = ("bank", "last4", "card_name", "network", "statement_day", "due_day")


def _day(name, value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 31:
        raise CardConfigError(f"{name} must be a whole day of the month, 1-31")
    return value


def _four_digits(name, value):
    v = str(value or "").strip()
    if not re.fullmatch(r"\d{4}", v):
        raise CardConfigError(f"{name} must be exactly 4 digits")
    return v


def _text(name, value, max_len, required=False):
    v = (value or "").strip() if isinstance(value, str) else value
    if v in (None, ""):
        if required:
            raise CardConfigError(f"{name} is required")
        return None
    if not isinstance(v, str) or len(v) > max_len:
        raise CardConfigError(f"{name} must be text of at most {max_len} characters")
    return v


def _card_row(conn, card_id):
    r = conn.execute(
        "SELECT id, bank, last4, card_name, network, billing_group, statement_day, due_day, "
        "pdf_card_last4, active, replaced_by_card_id FROM cards WHERE id=?", (card_id,)
    ).fetchone()
    return dict(r) if r else None


def update_card(conn, card_id, fields):
    """PUT /cards/{id}: edit a card's name, billing days, PDF alias, active flag or successor, then
    rewrite the cards config file so the edit survives restarts. Raises LookupError (404),
    CardConfigError (400)."""
    current = _card_row(conn, card_id)
    if not current:
        raise LookupError(f"card {card_id} not found")
    unknown = set(fields) - set(CARD_UPDATABLE)
    if unknown:
        raise CardConfigError(f"not editable: {', '.join(sorted(unknown))}")
    clean = {}
    for k, v in fields.items():
        if k == "card_name":
            clean[k] = _text(k, v, 60, required=True)
        elif k in ("statement_day", "due_day"):
            clean[k] = _day(k, v)
        elif k == "pdf_card_last4":
            clean[k] = None if v in (None, "") else _four_digits(k, v)
        elif k == "active":
            if v not in (True, False, 0, 1):
                raise CardConfigError("active must be true or false")
            clean[k] = 1 if v else 0
        elif k == "replaced_by_card_id":
            if v is not None:
                if isinstance(v, bool) or not isinstance(v, int) or v == card_id or not _card_row(conn, v):
                    raise CardConfigError("replaced_by_card_id must be the id of another existing card")
            clean[k] = v
    if clean:
        conn.execute(
            f"UPDATE cards SET {', '.join(f'{k}=?' for k in clean)} WHERE id=?", (*clean.values(), card_id)
        )
        conn.commit()
    exported = db_module.export_cards_config(conn)
    return {"card": _card_row(conn, card_id), "config_exported": exported}


def create_card(conn, fields):
    """POST /cards: add a card (e.g. a reissue with new last-4s), then rewrite the cards config file.
    Raises CardConfigError (400), CardConflict (409)."""
    unknown = set(fields) - set(CARD_CREATABLE)
    if unknown:
        raise CardConfigError(f"unknown fields: {', '.join(sorted(unknown))}")
    bank = _text("bank", fields.get("bank"), 30, required=True)
    last4 = _four_digits("last4", fields.get("last4"))
    card_name = _text("card_name", fields.get("card_name"), 60)
    network = _text("network", fields.get("network"), 30)
    statement_day = _day("statement_day", fields.get("statement_day"))
    due_day = _day("due_day", fields.get("due_day"))
    if conn.execute("SELECT 1 FROM cards WHERE bank=? AND last4=?", (bank, last4)).fetchone():
        raise CardConflict(f"{bank} card {last4} already exists")
    cur = conn.execute(
        "INSERT INTO cards (bank, last4, card_name, network, statement_day, due_day, active) "
        "VALUES (?, ?, ?, ?, ?, ?, 1)",
        (bank, last4, card_name, network, statement_day, due_day)
    )
    conn.commit()
    exported = db_module.export_cards_config(conn)
    return {"card": _card_row(conn, cur.lastrowid), "config_exported": exported}
