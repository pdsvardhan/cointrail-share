"""Analytics endpoints for dashboard - overview, categories, trends, heatmap.

iter-18 H5b / M13 (contract 4 of the iter-18 brief) — honest totals. Before, every endpoint
summed debits outside {Self Transfer, Finance}: refunds and cashback were never subtracted, an EMI
purchase counted again with every principal line, and money sent to people (40-80% of the
headline) was reported as spend. Now one definition is used everywhere:

  everyday spend = debits whose kind is purchase/debit/fee/interest/atm in a spend category
                 - credits whose kind is refund/reversal/cashback in a spend category

emi_principal / emi_conversion (the purchase itself was counted when it happened), bill_payment
and transfer_in never count. Money sent to people and investments are reported beside spend, as
`outflows`, never inside it. A row whose kind is still NULL (written before the iter-18 backfill)
is classified on the fly by modules.kinds_backfill.kind_for_row — the function the backfill
itself uses — so every number is the same before and after the backfill.

Calendar decisions ("today", days elapsed) are made in IST (util.timez).

Iteration 203 E1: every endpoint the Expenses page reads also takes a date range, `from` and `to`
(IST calendar dates, inclusive), as an alternative to `period=YYYY-MM`; the 3M / 6M / 1Y / All
pills used to change nothing but the page's look.
"""
import json
from collections import defaultdict
from datetime import date, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException, Query

from db import get_db
from modules.categorizer import card_numbers
from modules.kinds_backfill import kind_for_row
from util.timez import today_ist

router = APIRouter()

# ── What counts as spend ───────────────────────────────────────────────────────────────────
# The owner can change these groups: every analytics endpoint reads them from here and nowhere
# else. A category that is in none of the three sets is everyday spend.
EXCLUDED_CATEGORIES = frozenset({"Self Transfer", "Finance"})          # own money moving; card bills
SENT_TO_PEOPLE_CATEGORIES = frozenset({"Transfer", "Family Remittance"})
INVESTMENT_CATEGORIES = frozenset({"Investment", "Home Construction"})
# Repaying a loan (the SBI standing instruction to the loan account) is debt service, not everyday
# spending: Rs 75,000 + Rs 40,000 in one month would otherwise dwarf the month's real spend (iter-18).
LOAN_CATEGORIES = frozenset({"Loan Repayment"})

SPEND_DEBIT_KINDS = frozenset({"purchase", "debit", "fee", "interest", "atm"})
NETTING_CREDIT_KINDS = frozenset({"refund", "reversal", "cashback"})   # reduce their own category
FEE_KINDS = frozenset({"fee", "interest"})

GROUP_SPEND = "spend"
GROUP_SENT = "sent_to_people"
GROUP_INVEST = "investments"
GROUP_LOANS = "loans"
GROUP_EXCLUDED = "excluded"
OUTFLOW_GROUPS = (GROUP_SENT, GROUP_INVEST, GROUP_LOANS)

_COLUMNS = ("id, amount, date, category, merchant, raw_description, source, card_last4, "
            "txn_type, kind, raw_text, confidence")


def category_group(category):
    cat = category or "Uncategorized"
    if cat in EXCLUDED_CATEGORIES:
        return GROUP_EXCLUDED
    if cat in SENT_TO_PEOPLE_CATEGORIES:
        return GROUP_SENT
    if cat in INVESTMENT_CATEGORIES:
        return GROUP_INVEST
    if cat in LOAN_CATEGORIES:
        return GROUP_LOANS
    return GROUP_SPEND


def _get(row, key):
    """row[key] for a sqlite3.Row or dict that may not carry the column."""
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def classify(row, cards):
    """(group, signed_amount, kind) for a row that counts somewhere, else None.

    signed_amount is +amount for a counted debit and -amount for a refund/reversal/cashback that
    is netted. Credits net against spend categories; a REVERSAL (a payment that failed and came
    back) also nets inside the outflow group of the payment it undoes, once modules.pairing has
    tied it to that payment by bank reference and given it the payment's category (4 failed Rs 280
    UPI payments had stayed in "Sent to people"); an unpaired reversal nets nowhere."""
    kind = row["kind"] or kind_for_row(row, cards)
    group = category_group(row["category"])
    if (row["txn_type"] or "debit") == "credit":
        if kind in NETTING_CREDIT_KINDS and group == GROUP_SPEND:
            return group, -row["amount"], kind
        if kind == "reversal" and group in OUTFLOW_GROUPS and _get(row, "confidence") == "paired":
            return group, -row["amount"], kind
        return None
    if kind in SPEND_DEBIT_KINDS:
        return group, row["amount"], kind
    return None


class SpendTally:
    """Accumulates the spend definition over a set of rows."""

    def __init__(self):
        self.gross = 0.0
        self.refunds = 0.0
        self.fees_interest = 0.0
        self.count = 0
        self.outflows = {GROUP_SENT: [0.0, 0], GROUP_INVEST: [0.0, 0], GROUP_LOANS: [0.0, 0]}
        self.categories = defaultdict(lambda: {"total": 0.0, "count": 0, "gross": 0.0, "refunds": 0.0})
        self.cat_merchants = defaultdict(lambda: defaultdict(lambda: {"total": 0.0, "count": 0}))
        self.merchants = defaultdict(lambda: {"total": 0.0, "count": 0})
        self.daily = defaultdict(lambda: {"total": 0.0, "count": 0})
        self.debits = []          # counted spend debit rows, for top_transactions

    @property
    def total(self):
        return self.gross - self.refunds

    def add(self, row, cards, c=None):
        """Count `row`; `c` is its classify() result when the caller already has it."""
        c = c if c is not None else classify(row, cards)
        if c is None:
            return None
        group, signed, kind = c
        if group in self.outflows:
            self.outflows[group][0] += signed
            if signed > 0:
                self.outflows[group][1] += 1
            return c
        if group != GROUP_SPEND:
            return c
        cat = row["category"] or "Uncategorized"
        entry = self.categories[cat]
        entry["total"] += signed
        day = self.daily[row["date"]]
        day["total"] += signed
        cat_merchant = self.cat_merchants[cat][row["merchant"] or "Unknown"]
        cat_merchant["total"] += signed
        merchant = self.merchants[row["merchant"]] if row["merchant"] else None
        if merchant is not None:
            merchant["total"] += signed
        if signed > 0:
            self.gross += signed
            self.count += 1
            entry["count"] += 1
            entry["gross"] += signed
            day["count"] += 1
            cat_merchant["count"] += 1
            if merchant is not None:
                merchant["count"] += 1
            if kind in FEE_KINDS:
                self.fees_interest += signed
            self.debits.append(row)
        else:
            self.refunds -= signed
            entry["refunds"] -= signed
        return c

    def outflows_dict(self):
        return {g: {"total": _r(v[0]), "count": v[1]} for g, v in self.outflows.items()}

    def category_list(self, with_merchants=False):
        out = []
        for cat, v in self.categories.items():
            item = {"category": cat, "total": _r(v["total"]), "count": v["count"],
                    "gross": _r(v["gross"]), "refunds": _r(v["refunds"])}
            if with_merchants:
                item["merchants"] = sorted(
                    [{"name": k, "total": _r(m["total"]), "count": m["count"]}
                     for k, m in self.cat_merchants[cat].items()],
                    key=lambda x: -x["total"])[:5]
            out.append(item)
        out.sort(key=lambda x: -x["total"])
        return out

    def daily_list(self):
        return [{"date": d, "total": _r(v["total"]), "count": v["count"]}
                for d, v in sorted(self.daily.items()) if d]


def _r(x):
    return round(x + 0.0, 2)


def _fetch(conn, where, params):
    return conn.execute(
        f"SELECT {_COLUMNS} FROM transactions WHERE is_duplicate = 0 AND amount > 0 AND {where} "
        "ORDER BY date, id",
        params,
    ).fetchall()


def _tally(rows, cards):
    t = SpendTally()
    for r in rows:
        t.add(r, cards)
    return t


def _parse_period(period, today):
    if not period:
        return today.year, today.month
    try:
        year, month = (int(p) for p in period.split("-")[:2])
        date(year, month, 1)
    except ValueError:
        raise HTTPException(400, "period must be YYYY-MM")
    return year, month


def _month_bounds(year, month):
    first = date(year, month, 1)
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return first, nxt


def _prev_month(year, month):
    return (year - 1, 12) if month == 1 else (year, month - 1)


def days_elapsed(year, month, today):
    """Calendar days of the month up to and including `today` (IST): the whole month once it
    is over, 0 for a month that has not started."""
    first, nxt = _month_bounds(year, month)
    if today < first:
        return 0
    if today >= nxt:
        return (nxt - first).days
    return (today - first).days + 1


def _window_start(today, months):
    """The 1st of the month `months - 1` calendar months before `today`'s: 6 months on 20 Oct is
    1 May - 20 Oct. (It used to be the 1st of (today - 30 x months days): up to 7 calendar months.)"""
    index = today.year * 12 + today.month - 1 - (max(1, months) - 1)
    return date(index // 12, index % 12 + 1, 1).isoformat()


# ── Date ranges (iteration 203 E1) ─────────────────────────────────────────────────────────

FromParam = Annotated[Optional[str], Query(alias="from")]
ToParam = Annotated[Optional[str], Query(alias="to")]


def _iso_day(value, name):
    try:
        if len(value) != 10:
            raise ValueError(value)
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{name} must be a calendar date, YYYY-MM-DD")


def parse_range(date_from, date_to):
    """(first, last) inclusive IST dates for `from`/`to`, or None when neither is given."""
    if date_from is None and date_to is None:
        return None
    if date_from is None or date_to is None:
        raise HTTPException(400, "from and to go together")
    first, last = _iso_day(date_from, "from"), _iso_day(date_to, "to")
    if first > last:
        raise HTTPException(400, "from must not be after to")
    return first, last


def _window(period, date_from, date_to, today):
    """(first, last) inclusive dates of a request: its range, else its month (default: this IST month)."""
    rng = parse_range(date_from, date_to)
    if rng:
        return rng
    year, month = _parse_period(period, today)
    first, nxt = _month_bounds(year, month)
    return first, nxt - timedelta(days=1)


def _between(conn, first, last):
    return _fetch(conn, "date >= ? AND date <= ?", (first.isoformat(), last.isoformat()))


def _spend_rows(rows, cards):
    """(row, signed, kind) for each row behind the Spending figure (spend group: debits plus the
    refunds/cashback netted against them) — what the headline adds up."""
    out = []
    for r in rows:
        c = classify(r, cards)
        if c is not None and c[0] == GROUP_SPEND:
            out.append((r, c[1], c[2]))
    return out


def _txn_view(r, signed, kind):
    return {
        "id": r["id"], "amount": round(r["amount"], 2), "signed_amount": _r(signed), "date": r["date"],
        "merchant": r["merchant"] or r["raw_description"] or "Unknown", "category": r["category"],
        "raw_description": r["raw_description"], "source": r["source"], "card_last4": r["card_last4"],
        "txn_type": r["txn_type"] or "debit", "kind": kind,
    }


@router.get("/analytics/overview")
def analytics_overview(period: Optional[str] = None, date_from: FromParam = None, date_to: ToParam = None):
    conn = get_db()
    today = today_ist()
    cards = card_numbers(conn)
    rng = parse_range(date_from, date_to)

    if rng:
        # A range is compared with the window of the same number of days just before it. A range
        # that ends after today counts (and compares) only the days so far.
        first, last = rng
        end = min(last, today)
        days = max(0, (end - first).days + 1)
        cur = _tally(_between(conn, first, last), cards)
        prev_last = first - timedelta(days=1)
        prev_first = first - timedelta(days=max(days, 1))
        prev = _tally(_between(conn, prev_first, prev_last), cards)
        prev_same = prev.total
        head = {"period": None, "range": {"from": first.isoformat(), "to": last.isoformat()},
                "comparison_basis": "previous_window",
                "comparison_window": {"from": prev_first.isoformat(), "to": prev_last.isoformat(),
                                      "days": (prev_last - prev_first).days + 1}}
    else:
        year, month = _parse_period(period, today)
        first, nxt = _month_bounds(year, month)
        pfirst, pnxt = _month_bounds(*_prev_month(year, month))
        cur = _tally(_fetch(conn, "date >= ? AND date < ?", (first.isoformat(), nxt.isoformat())), cards)
        prev = _tally(_fetch(conn, "date >= ? AND date < ?", (pfirst.isoformat(), pnxt.isoformat())), cards)

        # "vs last month" compares like with like (iter-18 M13): the first N days of the previous
        # month while this month is in progress (Oct 1-20 vs Sep 1-20), the whole previous month once
        # this one is over. It used to set 18 days of October against all of September (-79.7%).
        days = days_elapsed(year, month, today)
        if days >= (nxt - first).days:
            prev_cutoff = pnxt
            basis = "previous_month"
        else:
            prev_cutoff = pfirst + timedelta(days=min(days, (pnxt - pfirst).days))
            basis = "same_days_last_month"
        prev_same = sum(v["total"] for d, v in prev.daily.items() if d and d < prev_cutoff.isoformat())
        head = {"period": f"{year}-{month:02d}", "range": None, "comparison_basis": basis}

    categories = cur.category_list()
    daily_spend = cur.daily_list()
    top = sorted(cur.debits, key=lambda r: (-r["amount"], r["date"], r["id"]))[:10]
    # The span of the data, so the page can anchor "All" and say when the newest data is older
    # than the month it opened on (iteration 203 E1 / E2 M2).
    span = conn.execute(
        "SELECT MIN(date) AS since, MAX(date) AS until FROM transactions "
        "WHERE is_duplicate = 0 AND amount > 0 AND date <= ?", (today.isoformat(),)
    ).fetchone()
    return {
        **head,
        "data_since": span["since"],
        "data_until": span["until"],
        "total": _r(cur.total),
        "gross_spend": _r(cur.gross),
        "refunds": _r(cur.refunds),
        "fees_interest": _r(cur.fees_interest),
        "outflows": cur.outflows_dict(),
        "txn_count": cur.count,
        "days_elapsed": days,
        # per calendar day of the period, not per day that happened to have a purchase
        "daily_avg": _r(cur.total / days) if days else 0.0,
        # the average purchase: gross, so a month's refunds do not shrink the typical ticket
        "avg_txn": _r(cur.gross / cur.count) if cur.count else 0.0,
        "prev_month_total": _r(prev.total),
        "prev_month_txn_count": prev.count,
        "prev_same_period_total": _r(prev_same),
        "categories": categories,
        "daily_spend": daily_spend,
        "highest_day": max(daily_spend, key=lambda x: x["total"]) if daily_spend else None,
        "top_category": categories[0] if categories else None,
        "top_transactions": [
            {"id": r["id"], "merchant": r["merchant"], "amount": r["amount"], "date": r["date"],
             "category": r["category"], "source": r["source"], "raw_description": r["raw_description"]}
            for r in top
        ],
    }


RECENT_LIMIT = 15


@router.get("/analytics/categories")
def analytics_categories(period: Optional[str] = None, category: Optional[str] = None,
                         date_from: FromParam = None, date_to: ToParam = None):
    conn = get_db()
    first, last = _window(period, date_from, date_to, today_ist())
    cards = card_numbers(conn)
    rows = _between(conn, first, last)

    if category:
        # Drill-down: exactly the rows behind this category's figure, newest first. `group` tells
        # the UI whether the category is spend, an outflow, or excluded. `recent_transactions` is the
        # category's newest rows whatever their merchant (signed: a refund is negative) — it used to
        # be rebuilt in the browser from at most 5 rows per merchant (iteration 203 E2 M4).
        merchant_totals = defaultdict(lambda: {"total": 0.0, "count": 0, "refunds": 0.0, "transactions": []})
        gross = refunds = 0.0
        recent = []
        for r in sorted(rows, key=lambda x: (x["date"] or "", x["id"]), reverse=True):
            if (r["category"] or "Uncategorized") != category:
                continue
            c = classify(r, cards)
            if c is None:
                continue
            _, signed, kind = c
            m = merchant_totals[r["merchant"] or r["raw_description"] or "Unknown"]
            m["total"] += signed
            if signed > 0:
                m["count"] += 1
                gross += signed
            else:
                m["refunds"] -= signed
                refunds -= signed
            if len(m["transactions"]) < 5:
                m["transactions"].append({"amount": r["amount"], "date": r["date"],
                                          "description": r["raw_description"],
                                          "txn_type": r["txn_type"] or "debit", "kind": kind})
            if len(recent) < RECENT_LIMIT:
                recent.append(_txn_view(r, signed, kind))
        merchants = sorted(
            [{"name": k, "total": _r(v["total"]), "count": v["count"], "refunds": _r(v["refunds"]),
              "transactions": v["transactions"]} for k, v in merchant_totals.items()],
            key=lambda x: -x["total"])
        return {"category": category, "group": category_group(category), "total": _r(gross - refunds),
                "gross": _r(gross), "refunds": _r(refunds), "merchants": merchants,
                "recent_transactions": recent}

    t = _tally(rows, cards)
    return {"total": _r(t.total), "categories": t.category_list(with_merchants=True)}


@router.get("/analytics/transactions")
def analytics_transactions(period: Optional[str] = None, date_from: FromParam = None, date_to: ToParam = None,
                           sort: str = "amount", offset: int = 0, limit: int = 50):
    """Every transaction behind the period's Spending figure, paged (iteration 203 E2 M1: "View All"
    opened the same top-10 list). Debits and the refunds netted against them, signed; transfers to
    people, investments, loans and own money are not spending and are not listed."""
    if sort not in ("amount", "date"):
        raise HTTPException(400, "sort must be amount or date")
    conn = get_db()
    first, last = _window(period, date_from, date_to, today_ist())
    listed = _spend_rows(_between(conn, first, last), card_numbers(conn))
    if sort == "amount":
        listed.sort(key=lambda x: (-x[1], x[0]["date"] or "", x[0]["id"]))
    else:
        listed.sort(key=lambda x: (x[0]["date"] or "", x[0]["id"]), reverse=True)
    offset, limit = max(0, offset), max(1, min(limit, 500))
    return {
        "range": {"from": first.isoformat(), "to": last.isoformat()},
        "sort": sort,
        "total_count": len(listed),
        "total": _r(sum(s for _, s, _ in listed)),
        "offset": offset,
        "transactions": [_txn_view(r, s, k) for r, s, k in listed[offset:offset + limit]],
    }


@router.get("/analytics/trends")
def analytics_trends(months: int = 12, date_from: FromParam = None, date_to: ToParam = None):
    conn = get_db()
    cards = card_numbers(conn)
    rng = parse_range(date_from, date_to)
    if rng:
        rows = _between(conn, *rng)
    else:
        rows = _fetch(conn, "date >= ?", (_window_start(today_ist(), months),))

    overall = SpendTally()
    monthly = defaultdict(SpendTally)
    for r in rows:
        if not r["date"] or len(r["date"]) < 7:
            continue
        c = classify(r, cards)
        if c is None or c[0] == GROUP_EXCLUDED:
            continue
        overall.add(r, cards, c)
        monthly[r["date"][:7]].add(r, cards, c)

    monthly_list = [
        {"month": k, "total": _r(t.total), "count": t.count, "gross_spend": _r(t.gross),
         "refunds": _r(t.refunds), "sent_to_people": _r(t.outflows[GROUP_SENT][0]),
         "investments": _r(t.outflows[GROUP_INVEST][0]), "loans": _r(t.outflows[GROUP_LOANS][0])}
        for k, t in sorted(monthly.items())
    ]
    cat_trends = defaultdict(dict)
    for month_key, t in monthly.items():
        for cat, v in t.categories.items():
            cat_trends[cat][month_key] = _r(v["total"])

    # Weekday distribution: net spend per day-of-week (0=Mon..6=Sun), averaged over the days of
    # that weekday that had any spend.
    dow = defaultdict(lambda: {"total": 0.0, "days": 0})
    for d, v in overall.daily.items():
        try:
            n = date.fromisoformat(d).weekday()
        except (TypeError, ValueError):
            continue
        dow[n]["total"] += v["total"]
        dow[n]["days"] += 1
    weekday_distribution = [
        {"dow": n, "avg_spend": _r(dow[n]["total"] / max(1, dow[n]["days"])),
         "total_spend": _r(dow[n]["total"]), "day_count": dow[n]["days"]}
        for n in range(7)
    ]

    top_merchants = sorted(
        [{"name": k, "total": _r(v["total"]), "count": v["count"]} for k, v in overall.merchants.items()],
        key=lambda x: -x["total"])[:10]

    return {
        "monthly": monthly_list,
        "category_trends": dict(cat_trends),
        "categories": sorted(overall.categories),
        "weekday_distribution": weekday_distribution,
        "top_merchants": top_merchants,
    }


@router.get("/analytics/heatmap")
def analytics_heatmap(months: int = 6, date_from: FromParam = None, date_to: ToParam = None):
    conn = get_db()
    rng = parse_range(date_from, date_to)
    if rng:
        rows = _between(conn, *rng)
    else:
        rows = _fetch(conn, "date >= ?", (_window_start(today_ist(), months),))
    t = _tally(rows, card_numbers(conn))
    return {"heatmap": t.daily_list()}


@router.get("/analytics/day-transactions")
def analytics_day_transactions(date: str):
    """Get all transactions for a specific day, for heatmap click-through.

    The rows behind that day's spend figure: counted debits, plus refunds/cashback (txn_type
    'credit', signed_amount negative) that reduce it."""
    conn = get_db()
    cards = card_numbers(conn)
    listed = []
    for r in _fetch(conn, "date = ?", (date,)):
        c = classify(r, cards)
        if c is None or c[0] != GROUP_SPEND:
            continue
        listed.append((r, c[1], c[2]))
    listed.sort(key=lambda x: (-x[0]["amount"], x[0]["id"]))
    return {
        "date": date,
        "total": _r(sum(s for _, s, _ in listed)),
        "count": len(listed),
        "transactions": [
            {
                "id": r["id"],
                "amount": round(r["amount"], 2),
                "signed_amount": _r(signed),
                "merchant": r["merchant"],
                "category": r["category"],
                "raw_description": r["raw_description"],
                "source": r["source"],
                "card_last4": r["card_last4"],
                "txn_type": r["txn_type"] or "debit",
                "kind": kind,
            }
            for r, signed, kind in listed
        ],
    }


@router.get("/analytics/merchant-detail")
def analytics_merchant_detail(merchant: str, months: int = 12, category: Optional[str] = None,
                              date_from: FromParam = None, date_to: ToParam = None):
    """One merchant's history, with product/restaurant info from order emails.

    `merchant` is the key the category drill shows (the merchant name, or the raw description when
    there is none) and is matched exactly, inside `category` when given; totals use the spend
    definition (refunds net, bill payments and EMI principal never count). The window is `from`/`to`,
    else the last `months` calendar months. (iteration 203 E2 B4: a LIKE %name% match pulled
    Swiggy Instamart into Swiggy, counted every debit kind and found nothing for a row keyed by its
    description.)"""
    conn = get_db()
    today = today_ist()
    rng = parse_range(date_from, date_to)
    first, last = rng if rng else (date.fromisoformat(_window_start(today, months)), today)
    cards = card_numbers(conn)

    listed = []
    for r in reversed(_between(conn, first, last)):
        if (r["merchant"] or r["raw_description"] or "Unknown") != merchant:
            continue
        if category is not None and (r["category"] or "Uncategorized") != category:
            continue
        c = classify(r, cards)
        if c is None or c[0] == GROUP_EXCLUDED:
            continue
        listed.append((r, c[1], c[2]))

    gross = sum(s for _, s, _ in listed if s > 0)
    refunds = -sum(s for _, s, _ in listed if s < 0)
    count = sum(1 for _, s, _ in listed if s > 0)

    enrichments = {}
    txn_ids = [r["id"] for r, _, _ in listed]
    if txn_ids:
        placeholders = ",".join("?" * len(txn_ids))
        for er in conn.execute(
            f"SELECT transaction_id, extracted_data FROM email_enrichments WHERE transaction_id IN ({placeholders})",
            txn_ids,
        ).fetchall():
            try:
                enrichments[er["transaction_id"]] = json.loads(er["extracted_data"])
            except Exception:
                pass

    monthly = defaultdict(lambda: {"total": 0.0, "count": 0})
    for r, signed, _ in listed:
        if r["date"] and len(r["date"]) >= 7:
            monthly[r["date"][:7]]["total"] += signed
            if signed > 0:
                monthly[r["date"][:7]]["count"] += 1

    transactions = []
    for r, signed, kind in listed:
        txn = _txn_view(r, signed, kind)
        if r["id"] in enrichments:
            txn["enrichment"] = enrichments[r["id"]]
        transactions.append(txn)

    return {
        "merchant": merchant,
        "category": category,
        "range": {"from": first.isoformat(), "to": last.isoformat()},
        "total": _r(gross - refunds),
        "gross": _r(gross),
        "refunds": _r(refunds),
        "count": count,
        "avg_transaction": _r(gross / count) if count else 0.0,
        "monthly": [{"month": k, "total": _r(v["total"]), "count": v["count"]} for k, v in sorted(monthly.items())],
        "transactions": transactions,
    }
