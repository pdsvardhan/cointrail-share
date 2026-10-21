"""
Recurring expense detection service.
Analyzes transaction history to identify subscriptions, EMIs, and recurring payments.
"""
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from db import get_db


# ═══════════════════════════════════════════════════════════════
# Helper functions
# ═══════════════════════════════════════════════════════════════

def _looks_like_person_name(name: str) -> bool:
    BRAND_KEYWORDS = {
        "ltd", "pvt", "inc", "corp", "llc", "bank", "pay", "mart", "store",
        "shop", "online", "digital", "tech", "services", "solutions", "india",
        "private", "limited", "payment", "recharge", "club", "enterprise",
    }
    words = name.strip().split()
    if len(words) < 2 or len(words) > 4:
        return False
    if not all(w[0].isupper() for w in words if len(w) > 1):
        return False
    if {w.lower() for w in words} & BRAND_KEYWORDS:
        return False
    return not any(c.isdigit() for c in name)


def _has_upi_vpa(raw: str) -> bool:
    return bool(re.search(r'@(okaxis|ybl|paytm|okhdfcbank|oksbi|ibl|axl|upi|apl|icici)', raw.lower()))


def _extract_hdfc_loan_id(raw: str) -> str | None:
    m = re.search(r'(\d{14,16})', raw)
    return m.group(1) if m else None


def _extract_hdfc_installment_num(raw: str) -> int | None:
    m = re.search(r'NB:(\d+)', raw)
    return int(m.group(1)) if m else None


def _is_principal_entry(raw: str) -> bool:
    lower = raw.lower()
    return ('prin' in lower or 'principal' in lower) and 'int' not in lower.replace('principal', '')


def _get_logo_domain(name: str) -> str | None:
    lower = name.lower()
    for k, v in _DOMAIN_MAP.items():
        if re.search(r"\b" + re.escape(k) + r"\b", lower) or k == lower:
            return v
    return None


def _compute_next_expected(sorted_dates: list[str], frequency: str = "monthly") -> str | None:
    if not sorted_dates:
        return None
    if len(sorted_dates) < 2 and frequency == "monthly":
        return None
    try:
        last_dt = datetime.strptime(sorted_dates[-1], "%Y-%m-%d")
        days = [int(d.split("-")[2]) for d in sorted_dates if len(d) >= 10]
        avg_day = min(round(sum(days) / len(days)), 28)
        if frequency == "biennial":
            next_dt = last_dt.replace(year=last_dt.year + 2, day=avg_day)
        elif frequency == "yearly":
            next_dt = last_dt.replace(year=last_dt.year + 1, day=avg_day)
        else:
            if last_dt.month == 12:
                next_dt = last_dt.replace(year=last_dt.year + 1, month=1, day=avg_day)
            else:
                next_dt = last_dt.replace(month=last_dt.month + 1, day=avg_day)
        return next_dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def _detect_frequency(sorted_dates: list[str]) -> str:
    if len(sorted_dates) < 2:
        return "monthly"
    intervals = []
    for i in range(1, len(sorted_dates)):
        try:
            d1 = datetime.strptime(sorted_dates[i - 1], "%Y-%m-%d")
            d2 = datetime.strptime(sorted_dates[i], "%Y-%m-%d")
            intervals.append((d2 - d1).days)
        except Exception:
            pass
    if not intervals:
        return "monthly"
    avg = sum(intervals) / len(intervals)
    if avg > 600:
        return "biennial"
    elif avg > 300:
        return "yearly"
    return "monthly"


# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

_SKIP_CATEGORIES = {"Groceries", "Food & Dining", "ATM Withdrawal", "Investment", "Finance"}

_KNOWN_SUBSCRIPTIONS = {
    "netflix", "spotify", "hotstar", "prime video", "youtube", "chatgpt",
    "openai", "anthropic", "claude", "github", "google", "apple", "microsoft",
    "notion", "figma", "vercel", "aws", "digitalocean", "zee5",
    "sony liv", "jiocinema", "adobe", "dropbox", "slack", "zoom",
    "linkedin", "icloud", "google one", "disney", "hbo",
    "porkbun", "proton", "zerodha", "jio", "google play",
    "youtube premium", "cursor", "amazon prime",
}

_KNOWN_FREQUENCIES = {
    "proton": "biennial",
    "hotstar": "yearly",
    "porkbun": "yearly",
    "amazon prime": "yearly",
    "youtube premium": "yearly",
}

_DOMAIN_MAP = {
    "netflix": "netflix.com", "spotify": "spotify.com", "hotstar": "hotstar.com",
    "prime": "primevideo.com", "youtube": "youtube.com", "chatgpt": "openai.com",
    "openai": "openai.com", "anthropic": "anthropic.com", "claude": "anthropic.com",
    "github": "github.com", "cursor": "cursor.com",
    "google": "google.com", "apple": "apple.com", "microsoft": "microsoft.com",
    "notion": "notion.so", "figma": "figma.com", "vercel": "vercel.com",
    "aws": "aws.amazon.com", "digitalocean": "digitalocean.com",
    "airtel": "airtel.in", "jio": "jio.com", "proton": "proton.me",
    "porkbun": "porkbun.com", "glokal": "glokal.co", "stelling": "stelling.co",
    "entomojo": "entomojo.com",
    "swiggy": "swiggy.com", "zomato": "zomato.com", "amazon": "amazon.in",
    "amazon prime": "amazon.in", "flipkart": "flipkart.com",
    "zerodha": "zerodha.com", "groww": "groww.in",
    "cred": "cred.club", "phonepe": "phonepe.com", "paytm": "paytm.com",
    "uber": "uber.com", "ola": "olacabs.com",
    "hdfc": "hdfcbank.com", "icici": "icicibank.com", "sbi": "sbi.co.in",
    "axis": "axisbank.com", "hsbc": "hsbc.co.in", "kotak": "kotak.com",
    "zee5": "zee5.com", "sony liv": "sonyliv.com", "jiocinema": "jiocinema.com",
    "google play": "play.google.com", "mokobara": "mokobara.com",
}


# ═══════════════════════════════════════════════════════════════
# Main detection function
# ═══════════════════════════════════════════════════════════════

# Staleness grace per frequency: a series with no transaction for longer than
# this is considered ended (subscription cancelled, EMI closed, autopay moved).
_STALE_AFTER_DAYS = {"monthly": 60, "yearly": 395, "biennial": 760}


# ═══════════════════════════════════════════════════════════════
# iter-203 E5: series the owner cancelled
# ═══════════════════════════════════════════════════════════════
# Every detected row carries a `series_key`: "sub:<merchant>" for a subscription/recurring payment
# (the same merchant grouping PART B uses), "emi:<group>" for an EMI line group, "plan:<id>" for an EMI
# plan (E6). A cancellation is stored against that key, so it survives re-detection.

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CHARGE_SOURCES = ("sms", "card", "pdf", "email")
# A UPI AutoPay / e-mandate verification debit (Rs 1-2) is a check, not a charge of the series.
MIN_SERIES_CHARGE = 10


def _series_text(merchant, raw):
    return re.sub(r"\s+", " ", (merchant or raw or "").lower().strip())


def list_cancellations(conn=None):
    conn = conn or get_db()
    try:
        rows = conn.execute(
            "SELECT id, series_key, display_name, cancelled_on, notes, created_at "
            "FROM recurring_cancellations ORDER BY cancelled_on DESC, id DESC"
        ).fetchall()
    except Exception:
        return []          # a database from before iter-203 has no table yet
    return [dict(r) for r in rows]


def cancel_series(conn, series_key, cancelled_on=None, display_name=None, notes=None):
    """Mark a series cancelled as of `cancelled_on` (YYYY-MM-DD, default today IST). Re-cancelling the
    same series moves its date. Raises ValueError on a missing key or a malformed date."""
    from util.timez import today_ist

    key = (series_key or "").strip()
    if not key:
        raise ValueError("series_key is required")
    day = (cancelled_on or today_ist().isoformat()).strip()
    if not _ISO_DAY.match(day):
        raise ValueError("cancelled_on must be YYYY-MM-DD")
    datetime.strptime(day, "%Y-%m-%d")
    conn.execute(
        "INSERT INTO recurring_cancellations (series_key, display_name, cancelled_on, notes) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(series_key) DO UPDATE SET cancelled_on = excluded.cancelled_on, "
        "display_name = COALESCE(excluded.display_name, recurring_cancellations.display_name), "
        "notes = COALESCE(excluded.notes, recurring_cancellations.notes)",
        (key, display_name, day, notes),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM recurring_cancellations WHERE series_key = ?", (key,)).fetchone())


def uncancel_series(conn, cancellation_id):
    """Undo a cancellation. Returns the removed row, or None when there is no such cancellation."""
    row = conn.execute("SELECT * FROM recurring_cancellations WHERE id = ?", (cancellation_id,)).fetchone()
    if not row:
        return None
    conn.execute("DELETE FROM recurring_cancellations WHERE id = ?", (cancellation_id,))
    conn.commit()
    return dict(row)


def _charges_after(conn, series_key, cancelled_on):
    """Debits of a cancelled subscription dated after its cancellation (any source, live rows only). A
    mandate verification debit (below MIN_SERIES_CHARGE) is not a charge (verifier round 2)."""
    if not series_key.startswith("sub:"):
        return []
    text = series_key[4:]
    rows = conn.execute(
        "SELECT id, date, amount, merchant, raw_description, card_last4 FROM transactions "
        "WHERE date > ? AND txn_type = 'debit' AND is_duplicate = 0 AND amount >= ? "
        f"AND source IN ({','.join('?' * len(_CHARGE_SOURCES))}) ORDER BY date",
        (cancelled_on, MIN_SERIES_CHARGE, *_CHARGE_SOURCES),
    ).fetchall()
    return [{"id": r["id"], "date": r["date"], "amount": r["amount"], "card_last4": r["card_last4"]}
            for r in rows if _series_text(r["merchant"], r["raw_description"]) == text]


def cancelled_series_charged_again(conn=None):
    """Cancelled series that charged after their cancellation date (E5 alert):
    [{series_key, merchant, cancelled_on, charges: [{id, date, amount, card_last4}]}]."""
    conn = conn or get_db()
    out = []
    for c in list_cancellations(conn):
        charges = _charges_after(conn, c["series_key"], c["cancelled_on"])
        if charges:
            out.append({"series_key": c["series_key"],
                        "merchant": c["display_name"] or c["series_key"].split(":", 1)[-1],
                        "cancelled_on": c["cancelled_on"], "charges": charges})
    return out


def _apply_cancellations(conn, results):
    """Mark detected rows whose series the owner cancelled, and list cancellations no longer detected.
    Returns the cancelled rows (detected first)."""
    cancellations = {c["series_key"]: c for c in list_cancellations(conn)}
    cancelled = []
    for r in results:
        c = cancellations.pop(r["series_key"], None)
        if not c:
            continue
        r.update(status="cancelled", cancelled_on=c["cancelled_on"], cancellation_id=c["id"],
                 next_expected=None, detected=True,
                 charged_after_cancel=_charges_after(conn, r["series_key"], c["cancelled_on"]))
        cancelled.append(r)
    for key, c in cancellations.items():
        name = c["display_name"] or key.split(":", 1)[-1]
        cancelled.append({
            "series_key": key, "merchant": name, "display_name": name, "status": "cancelled",
            "cancelled_on": c["cancelled_on"], "cancellation_id": c["id"], "detected": False,
            "next_expected": None, "charged_after_cancel": _charges_after(conn, key, c["cancelled_on"]),
        })
    return cancelled


def _lifecycle_status(row) -> str:
    """Classify a detected row: active | ended | completed."""
    if (row.get("type") == "emi" and row.get("total_installments")
            and row.get("payments_done")
            and row["payments_done"] >= row["total_installments"]):
        return "completed"
    last = row.get("last_date")
    if not last:
        return "ended"
    try:
        age = (datetime.now() - datetime.strptime(last, "%Y-%m-%d")).days
    except Exception:
        return "active"
    limit = _STALE_AFTER_DAYS.get(row.get("frequency") or "monthly", 60)
    return "ended" if age > limit else "active"


def detect_recurring(include_ended: bool = False):
    """Analyze transactions to auto-detect recurring expenses, subscriptions, and EMIs.

    Rows are annotated with `status` (active/ended/completed). By default only
    active rows are returned — ended subscriptions and finished EMIs would
    otherwise linger forever with a past-dated `next_expected` that the UI
    renders as if upcoming (2026-07-14 audit). Pass include_ended=True to see
    everything.
    """
    conn = get_db()

    # iter-203 E6: an EMI plan is one item built from its booking + statement lines; its lines
    # (instalments, interest, processing fee) are never grouped again below.
    from services.emi_plans import detect_emi_plans
    plans = detect_emi_plans(conn)
    plan_txn_ids = {tid for p in plans for tid in p["txn_ids"]}

    rows = conn.execute(
        "SELECT id, merchant, date, amount, category, raw_description, card_last4 "
        "FROM transactions "
        "WHERE date >= date('now', '-30 months') "
        "AND source IN ('sms', 'card', 'pdf') "
        "AND txn_type = 'debit' AND is_duplicate = 0 AND amount > 0 "
        "ORDER BY merchant, date"
    ).fetchall()
    rows = [r for r in rows if r["id"] not in plan_txn_ids]

    emi_rows = []
    regular_rows = []

    EMI_KEYWORDS_RE = re.compile(
        r'\b(emi|amortization|principal debit adjustment)\b|offus emi', re.IGNORECASE
    )

    for r in rows:
        raw = r["raw_description"] or ""
        merchant = r["merchant"] or ""
        if EMI_KEYWORDS_RE.search(raw) or EMI_KEYWORDS_RE.search(merchant):
            emi_rows.append(dict(r))
        else:
            regular_rows.append(dict(r))

    results = []

    # PART A: EMI Detection
    emi_groups = defaultdict(list)

    for r in emi_rows:
        raw = r["raw_description"] or ""
        card = r["card_last4"] or "unknown"
        merchant = r["merchant"] or ""

        loan_id = _extract_hdfc_loan_id(raw)
        if loan_id:
            emi_groups["hdfc_" + loan_id].append(r)
            continue
        if raw.upper().startswith("EMI ") and "amortization" not in raw.lower() and "principal debit" not in raw.lower():
            emi_groups["card_emi_" + card + "_" + merchant.lower()[:20]].append(r)
            continue
        if "sbi" in raw.lower() and "emi" in raw.lower():
            emi_groups["sbi_emi_" + card + "_" + merchant.lower()[:20]].append(r)
            continue
        if "amortization" in raw.lower():
            emi_groups["amort_" + card].append(r)
            continue
        if "principal debit" in raw.lower():
            emi_groups["principal_adj_" + card].append(r)
            continue
        emi_groups["emi_other_" + card].append(r)

    for group_key, txns in emi_groups.items():
        if not txns or group_key.startswith("card_emi_") or group_key.startswith("sbi_emi_"):
            continue

        principal_txns = [t for t in txns if _is_principal_entry(t["raw_description"] or "")]
        if not principal_txns:
            principal_txns = txns

        amounts = [t["amount"] for t in principal_txns]
        all_amounts = [t["amount"] for t in txns]
        sorted_dates = sorted(t["date"] for t in principal_txns if t["date"])
        months = sorted(set(t["date"][:7] for t in principal_txns if t["date"] and len(t["date"]) >= 7))

        if len(months) < 2:
            continue

        avg_amount = sum(amounts) / len(amounts) if amounts else 0
        card_last4 = txns[0].get("card_last4")

        installment_nums = []
        for t in txns:
            nb = _extract_hdfc_installment_num(t["raw_description"] or "")
            if nb is not None:
                installment_nums.append(nb)
        total_installments = max(installment_nums) if installment_nums else None
        payments_done = len(months)

        product_name = None
        display_name = None

        if group_key.startswith("hdfc_"):
            loan_id_str = group_key.replace("hdfc_", "")
            if sorted_dates:
                try:
                    orig_rows = conn.execute(
                        "SELECT merchant FROM transactions "
                        "WHERE card_last4 = ? AND txn_type = 'debit' AND is_duplicate = 0 "
                        "AND date <= ? AND date >= date(?, '-8 months') AND amount >= ? "
                        "AND LOWER(COALESCE(raw_description,'')) NOT LIKE '%emi%' "
                        "AND LOWER(COALESCE(raw_description,'')) NOT LIKE '%amortization%' "
                        "ORDER BY amount DESC LIMIT 3",
                        (card_last4, sorted_dates[0], sorted_dates[0], avg_amount * 2)
                    ).fetchall()
                    if orig_rows:
                        product_name = orig_rows[0]["merchant"]
                except Exception:
                    pass
            display_name = product_name + " (EMI)" if product_name else "EMI Loan " + loan_id_str[-6:]
            merchant_display = display_name
        elif group_key.startswith("amort_"):
            display_name = "Card " + (card_last4 or "?") + " EMI"
            if sorted_dates:
                try:
                    orig_rows = conn.execute(
                        "SELECT merchant FROM transactions "
                        "WHERE card_last4 = ? AND txn_type = 'debit' AND is_duplicate = 0 "
                        "AND date <= ? AND date >= date(?, '-3 months') AND amount >= ? "
                        "AND LOWER(COALESCE(raw_description,'')) NOT LIKE '%amortization%' "
                        "AND LOWER(COALESCE(raw_description,'')) NOT LIKE '%emi%' "
                        "AND LOWER(COALESCE(merchant,'')) != 'principal amortization' "
                        "AND LOWER(COALESCE(merchant,'')) != 'interest amortization' "
                        "ORDER BY amount DESC LIMIT 3",
                        (card_last4, sorted_dates[0], sorted_dates[0], avg_amount * 3)
                    ).fetchall()
                    if orig_rows:
                        product_name = orig_rows[0]["merchant"]
                        display_name = product_name + " (EMI)"
                except Exception:
                    pass
            merchant_display = display_name
        elif group_key.startswith("principal_adj_"):
            display_name = "Card " + (card_last4 or "?") + " EMI"
            merchant_display = display_name
        else:
            merchant_names = Counter(t["merchant"] for t in txns)
            merchant_display = merchant_names.most_common(1)[0][0]
            display_name = merchant_display + " (EMI)"

        next_expected = _compute_next_expected(sorted_dates)
        if total_installments and installment_nums and max(installment_nums) >= total_installments:
            next_expected = None
            payments_done = total_installments

        results.append({
            "series_key": "emi:" + group_key,
            "merchant": merchant_display, "category": "EMI & Loans", "type": "emi",
            "frequency": "monthly", "avg_amount": round(avg_amount, 2),
            "occurrences": len(principal_txns), "months": months,
            "last_date": sorted_dates[-1] if sorted_dates else None,
            "next_expected": next_expected, "total_paid": round(sum(all_amounts), 2),
            "logo_domain": _get_logo_domain(display_name), "card_last4": card_last4,
            "payments_done": payments_done, "total_installments": total_installments,
            "product_name": product_name, "display_name": display_name,
        })

    # PART B: Subscriptions & Recurring
    groups = defaultdict(list)
    for r in regular_rows:
        merchant = (r["merchant"] or r["raw_description"] or "").strip()
        if not merchant:
            continue
        key = re.sub(r"\s+", " ", merchant.lower().strip())
        groups[key].append({
            "merchant": merchant, "date": r["date"], "amount": r["amount"],
            "category": r["category"], "raw": r["raw_description"] or "",
            "card_last4": r["card_last4"],
        })

    for key, txns in groups.items():
        # A mandate's Rs 1-2 verification debit (UPI AutoPay / e-mandate set-up) is not a charge of the
        # series. It used to make min(amounts) < 10 below and drop the whole series: LinkedIn vanished
        # from Recurring once its Rs 2 set-up debit took the same merchant name (iter-203 E5).
        txns = [t for t in txns if t["amount"] >= MIN_SERIES_CHARGE]
        if not txns:
            continue
        months = sorted(set(t["date"][:7] for t in txns if t["date"] and len(t["date"]) >= 7))
        merchant_names = Counter(t["merchant"] for t in txns)
        display_name = merchant_names.most_common(1)[0][0]
        lower_name = display_name.lower()

        categories = Counter(t["category"] for t in txns if t["category"] and t["category"] != "Uncategorized")
        category = categories.most_common(1)[0][0] if categories else "Uncategorized"
        is_known_sub = any(s in lower_name for s in _KNOWN_SUBSCRIPTIONS)

        if category == "Transfer":
            if _looks_like_person_name(display_name) or any(_has_upi_vpa(t["raw"]) for t in txns):
                continue
        if category in _SKIP_CATEGORIES and not is_known_sub:
            continue

        min_months = 1 if is_known_sub else 3
        if len(months) < min_months:
            continue

        amounts = [t["amount"] for t in txns]
        avg_amount = sum(amounts) / len(amounts)
        if min(amounts) < 10:
            continue

        variance = (max(amounts) - min(amounts)) / min(amounts)
        if not is_known_sub and variance > 0.3:
            continue

        if not is_known_sub:
            sorted_dates_check = sorted(t["date"] for t in txns if t["date"])
            if len(sorted_dates_check) >= 3:
                intervals = []
                for i in range(1, len(sorted_dates_check)):
                    try:
                        d1 = datetime.strptime(sorted_dates_check[i - 1], "%Y-%m-%d")
                        d2 = datetime.strptime(sorted_dates_check[i], "%Y-%m-%d")
                        intervals.append((d2 - d1).days)
                    except Exception:
                        pass
                if intervals:
                    avg_interval = sum(intervals) / len(intervals)
                    if not (15 <= avg_interval <= 50 or 300 <= avg_interval <= 400 or 600 <= avg_interval <= 800):
                        continue

        rtype = "subscription" if (is_known_sub or variance <= 0.05) else "recurring"

        sorted_dates = sorted(t["date"] for t in txns if t["date"])
        frequency = None
        for kf_key, kf_val in _KNOWN_FREQUENCIES.items():
            if kf_key in lower_name:
                frequency = kf_val
                break
        if not frequency:
            frequency = _detect_frequency(sorted_dates)

        card_counts = Counter(t["card_last4"] for t in txns if t["card_last4"])
        # what the series last cost: a cancelled (or re-priced) series is shown at its latest charge, not
        # at the average of older, cheaper ones (verifier round 2)
        latest = max((t for t in txns if t["date"]), key=lambda t: t["date"], default=None)

        results.append({
            "series_key": "sub:" + key,
            "merchant": display_name, "category": category, "type": rtype,
            "frequency": frequency, "avg_amount": round(avg_amount, 2),
            "last_amount": round(latest["amount"], 2) if latest else None,
            "occurrences": len(txns), "months": months,
            "last_date": sorted_dates[-1] if sorted_dates else None,
            "next_expected": _compute_next_expected(sorted_dates, frequency),
            "total_paid": round(sum(amounts), 2),
            "logo_domain": _get_logo_domain(display_name),
            "card_last4": card_counts.most_common(1)[0][0] if card_counts else None,
            "payments_done": None, "total_installments": None,
            "product_name": None, "display_name": display_name,
        })

    for p in plans:
        months = sorted({i["date"][:7] for i in p["installments"]})
        display = f"{p['merchant']} (EMI)"
        results.append({
            "series_key": p["series_key"],
            "merchant": display, "category": "EMI & Loans", "type": "emi",
            "frequency": "monthly", "avg_amount": p["monthly_payment"] or 0.0,
            "occurrences": len(p["installments"]), "months": months,
            "last_date": p["last_installment_date"], "next_expected": p["next_due"],
            "total_paid": p["total_paid"], "logo_domain": _get_logo_domain(p["merchant"]),
            "card_last4": p["card_last4"], "payments_done": p["installments_paid"],
            "total_installments": p["tenure_months"], "product_name": p["merchant"],
            "display_name": display, "emi_plan": p,
        })

    for r in results:
        # an EMI plan knows its own state from the bank's schedule (services.emi_plans)
        r["status"] = r["emi_plan"]["status"] if "emi_plan" in r else _lifecycle_status(r)
        if r["status"] != "active":
            # An ended/completed series has no upcoming charge — never render one.
            r["next_expected"] = None

    # iter-203 E5: a series the owner cancelled leaves the active list (and every total built from it)
    # whatever its charges say; it is reported under `cancelled`, with any charge after the date.
    cancelled = _apply_cancellations(conn, results)
    counts = Counter(r["status"] for r in results)
    counts["cancelled"] = len(cancelled)
    results = [r for r in results if r["status"] != "cancelled"]
    if not include_ended:
        results = [r for r in results if r["status"] == "active"]

    type_order = {"subscription": 0, "emi": 1, "recurring": 2}
    results.sort(key=lambda x: (type_order.get(x["type"], 3), -x["avg_amount"]))
    return {"recurring": results, "cancelled": cancelled, "status_counts": dict(counts)}
