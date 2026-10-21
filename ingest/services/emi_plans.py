"""EMI plans — iter-203 E6.

A purchase converted to EMIs used to surface on the Recurring page as whatever line the grouping
caught: on SBI, "INTEREST ON EMI ₹291.89/mo" — interest only, no purchase, no monthly payment. A plan
is now one item built from the evidence the banks give:

  booking   SBI "Details of Merchant EMI booked" e-mail (amount, purchase date, rate, tenure, fee, EMI
            amount, first EMI date) and SMS ("Your EMI Booking amount of Rs.X at <merchant> has been
            converted to N EMIs at R percent ..."); ICICI "has been converted into EMI" SMS / "EMI on
            Card" e-mail (card, amount, date).
  lines     statement lines: SBI "FP EMI 01/06(EXCL TAX 93.49)" (the instalment, principal + interest,
            tax printed alongside), "INTEREST ON EMI" (that instalment's interest), "MERCHANT EMI PROC
            FEE"; ICICI "Principal / Interest Amount Amortization - <x/n>MERCHANT" (the marker is
            sometimes missing); HDFC "OFFUS EMI,PRIN NB:x,<loan>" / "OFFUS EMI,INT NBR:x,<loan>".
  purchase  the card transaction the booking converted (merchant name).

What counts as spend is unchanged: the purchase counted once when it was made, instalment principal
excluded, interest and fees counted (txn_store.derive_kind).

"Paid" follows the bank's schedule: an instalment is charged to the card on its date even before the
statement that prints it arrives, so installments_paid = max(instalments on statements, scheduled
dates up to today); installments_billed says how many the statements show.
"""
import html
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from util.timez import today_ist

_AMT = r"([\d,]+(?:\.\d+)?)"

_SBI_INSTALMENT = re.compile(
    r"^\s*(?P<code>[A-Z]{1,4})\s*EMI\s*(?P<x>\d{1,2})\s*/\s*(?P<n>\d{1,2})\s*\(\s*EXCL\s*TAX\s*(?P<tax>[\d,.]+)\s*\)",
    re.I)
_SBI_INTEREST = re.compile(r"^\s*INTEREST\s+ON\s+EMI\b", re.I)
_SBI_FEE = re.compile(r"\bEMI\s+PROC(?:ESSING)?\.?\s+FEE\b(?:.*?EXCL\s*TAX\s*(?P<tax>[\d,.]+))?", re.I)
_ICICI_LINE = re.compile(
    r"^\s*(?P<part>Principal|Interest)\s+Amount\s+Amorti[sz]ation\s*-\s*"
    r"(?:<\s*(?P<x>\d+)\s*/\s*(?P<n>\d+)\s*>\s*(?P<m>.*?))?\s*0?\s*$", re.I)
_HDFC_LINE = re.compile(r"OFFUS\s+EMI\s*,\s*(?P<part>PRIN|INT)\s+NBR?\s*:\s*(?P<x>\d+)\s*,\s*(?P<loan>\d+)", re.I)

_SBI_EMAIL = {
    "amount": re.compile(r"Booking\s+Amount\s*:\s*Rs\.?\s*" + _AMT, re.I),
    "txn_amount": re.compile(r"Transaction\s+Amount\s*:\s*Rs\.?\s*" + _AMT, re.I),
    "txn_date": re.compile(r"Transaction\s+Date\s*\(DD/MM/YYYY\)*\s*:\s*(\d{2}/\d{2}/\d{4})", re.I),
    "rate": re.compile(r"Rate\s+of\s+Interest\s*:\s*([\d.]+)\s*%", re.I),
    "tenure": re.compile(r"Tenure\s*:\s*(\d+)\s*months?", re.I),
    "fee": re.compile(r"Processing\s+Fee[^:]*:\s*Rs\.?\s*" + _AMT, re.I),
    "emi": re.compile(r"EMI\s+Amount\s*:\s*Rs\.?\s*" + _AMT, re.I),
    "first": re.compile(r"First\s+EMI\s+Date\s*\(DD/MM/YYYY\)*\s*:\s*(\d{2}/\d{2}/\d{4})", re.I),
}
_SBI_SMS = re.compile(
    r"EMI\s+Booking\s+amount\s+of\s+Rs\.?\s*" + _AMT + r"\s+at\s+(?P<m>.+?)\s+has\s+been\s+converted\s+to\s+"
    r"(?P<n>\d+)\s+EMIs?\s+at\s+(?P<rate>[\d.]+)\s+percent.*?Processing\s+Fee\s+of\s+Rs\.?\s*" + _AMT
    + r"\s+on\s+(?P<d>\d{2}/\d{2}/\d{4})", re.I | re.S)
_ICICI_SMS = re.compile(
    r"transaction\s+of\s+Rs\.?\s*" + _AMT + r"\s+using\s+ICICI\s+Bank\s+Credit\s+Card\s+XX(?P<card>\d{4})\s+"
    r"has\s+been\s+converted\s+into\s+EMI\s+on\s+(?P<d>\d{2}-\d{2}-\d{2,4})", re.I)
_ICICI_EMAIL = re.compile(
    r"converted\s+the\s+purchase\s+made\s+on\s+your\s+Credit\s+Card\s+\d{4}X+(?P<card>\d{4})\s+for\s+Rs\.?\s*"
    + _AMT + r"\s+on\s+(?P<d>[A-Za-z]+\s+\d{1,2},\s+\d{4})\s+into\s+Equated", re.I)

STALE_AFTER_DAYS = 75          # an instalment line is monthly; > 2 statements without one = over


def _num(s):
    return float(str(s).replace(",", "").rstrip("."))


def _dmy(s):
    return datetime.strptime(s, "%d/%m/%Y").date().isoformat()


def _add_months(iso, k):
    d = date.fromisoformat(iso[:10])
    y, m = divmod(d.month - 1 + k, 12)
    y, m = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day).isoformat()
        except ValueError:
            continue
    return None


def _month_add(ym, k):
    return _add_months(ym + "-01", k)[:7]


def _months_between(a, b):
    da, db = date.fromisoformat(a[:10]), date.fromisoformat(b[:10])
    return (db.year - da.year) * 12 + db.month - da.month


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


# --- bookings --------------------------------------------------------------------------------------

def _bookings(conn):
    out = []
    for e in conn.execute(
        "SELECT id, sender_email, subject, body_text, body_snippet FROM emails "
        "WHERE (lower(sender_email) LIKE '%sbicard%' AND (subject LIKE '%Merchant EMI%' OR body_text LIKE '%Booking Amount%')) "
        "OR (lower(sender_email) LIKE '%icici%' AND (subject LIKE '%EMI on Card%' OR body_text LIKE '%into Equated Monthly%'))"
    ).fetchall():
        text = " ".join(html.unescape(e["body_text"] or e["body_snippet"] or "").replace("\xa0", " ").split())
        if "sbicard" in (e["sender_email"] or "").lower():
            f = {k: rx.search(text) for k, rx in _SBI_EMAIL.items()}
            if not (f["amount"] or f["txn_amount"]) or not f["tenure"]:
                continue
            out.append({
                "bank": "SBI", "card": None, "source": "booking_email", "ref": e["id"],
                "amount": _num((f["amount"] or f["txn_amount"]).group(1)),
                "txn_date": _dmy(f["txn_date"].group(1)) if f["txn_date"] else None,
                "tenure": int(f["tenure"].group(1)),
                "rate": float(f["rate"].group(1)) if f["rate"] else None,
                "fee": _num(f["fee"].group(1)) if f["fee"] else None,
                "emi_amount": _num(f["emi"].group(1)) if f["emi"] else None,
                "first_emi_date": _dmy(f["first"].group(1)) if f["first"] else None,
                "merchant_hint": None,
            })
        else:
            m = _ICICI_EMAIL.search(text)
            if m:
                d = datetime.strptime(m.group("d"), "%B %d, %Y").date().isoformat()
                out.append({"bank": "ICICI", "card": m.group("card"), "source": "booking_email", "ref": e["id"],
                            "amount": _num(m.group(2)), "txn_date": d, "tenure": None, "rate": None, "fee": None,
                            "emi_amount": None, "first_emi_date": None, "merchant_hint": None})
    for s in conn.execute(
        "SELECT id, body FROM sms WHERE body LIKE '%EMI Booking amount%' OR body LIKE '%converted into EMI%'"
    ).fetchall():
        body = " ".join((s["body"] or "").split())
        m = _SBI_SMS.search(body)
        if m:
            out.append({"bank": "SBI", "card": None, "source": "booking_sms", "ref": s["id"],
                        "amount": _num(m.group(1)), "txn_date": None, "booking_date": _dmy(m.group("d")),
                        "tenure": int(m.group("n")), "rate": float(m.group("rate")), "fee": _num(m.group(5)),
                        "emi_amount": None, "first_emi_date": None, "merchant_hint": m.group("m").strip()})
            continue
        m = _ICICI_SMS.search(body)
        if m:
            raw = m.group("d")
            fmt = "%d-%m-%y" if len(raw.split("-")[-1]) == 2 else "%d-%m-%Y"
            out.append({"bank": "ICICI", "card": m.group("card"), "source": "booking_sms", "ref": s["id"],
                        "amount": _num(m.group(1)), "txn_date": datetime.strptime(raw, fmt).date().isoformat(),
                        "tenure": None, "rate": None, "fee": None, "emi_amount": None, "first_emi_date": None,
                        "merchant_hint": None})
    return _merge_bookings(out)


def _merge_bookings(items):
    """E-mail and SMS of one booking describe the same thing: one record with every field either gave."""
    merged = []
    for b in items:
        b_day = b.get("txn_date") or b.get("booking_date")
        twin = None
        for m in merged:
            m_day = m.get("txn_date") or m.get("booking_date")
            if (m["bank"] == b["bank"] and abs(m["amount"] - b["amount"]) <= 1.0
                    and (not m["card"] or not b["card"] or m["card"] == b["card"])
                    and b_day and m_day and abs((date.fromisoformat(b_day) - date.fromisoformat(m_day)).days) <= 7):
                twin = m
                break
        if twin is None:
            merged.append(dict(b, sources=[b["source"]]))
            continue
        for k, v in b.items():
            if twin.get(k) in (None, "") and v not in (None, ""):
                twin[k] = v
        if b["source"] not in twin["sources"]:
            twin["sources"].append(b["source"])
    return merged


def _card_sets(conn):
    by_bank = defaultdict(set)
    for r in conn.execute("SELECT bank, last4, pdf_card_last4 FROM cards").fetchall():
        for v in (r["last4"], r["pdf_card_last4"]):
            if v:
                by_bank[(r["bank"] or "").upper()].add(str(v))
    return by_bank


def _purchase(conn, cards, amount, day):
    """The card purchase a booking converted: same amount (±₹1), ±3 days, one of the bank's cards."""
    if not cards or not day:
        return None
    lo = (date.fromisoformat(day) - timedelta(days=3)).isoformat()
    hi = (date.fromisoformat(day) + timedelta(days=3)).isoformat()
    rows = conn.execute(
        f"SELECT id, date, amount, merchant, raw_description, card_last4, source FROM transactions "
        f"WHERE txn_type = 'debit' AND is_duplicate = 0 AND ABS(amount - ?) <= 1.0 AND date BETWEEN ? AND ? "
        f"AND card_last4 IN ({','.join('?' * len(cards))}) "
        f"AND COALESCE(kind, '') NOT IN ('emi_principal', 'interest', 'fee') "
        f"ORDER BY (source = 'card') DESC, ABS(julianday(date) - julianday(?)), id",
        (amount, lo, hi, *sorted(cards), day),
    ).fetchall()
    return rows[0] if rows else None


# --- statement lines -------------------------------------------------------------------------------

def _new_plan(bank, card, tag, tenure):
    return {"bank": bank, "card": card, "tag": tag, "tenure": tenure, "installments": {}, "extra_ids": [],
            "fee": None, "fee_tax": 0.0, "booking": None, "merchant": None}


def _inst(plan, x, day):
    i = plan["installments"].get(x)
    if i is None:
        i = plan["installments"][x] = {"n": x, "date": day, "principal": None, "interest": 0.0, "tax": 0.0,
                                       "amount": None, "txn_ids": []}
    return i


def _sbi_plans(lines):
    plans, interests, fees = [], [], []
    for t in lines:
        desc = t["raw_description"] or ""
        m = _SBI_INSTALMENT.search(desc)
        if m:
            x, n = int(m.group("x")), int(m.group("n"))
            code = m.group("code").upper()
            open_ = [p for p in plans if (p["card"], p["tag"], p["tenure"]) == (t["card_last4"], code, n)
                     and max(p["installments"]) == x - 1
                     and 0 < (date.fromisoformat(t["date"]) - date.fromisoformat(
                         p["installments"][x - 1]["date"])).days <= 45]
            plan = open_[-1] if open_ else None
            if plan is None:
                plan = _new_plan("SBI", t["card_last4"], code, n)
                plans.append(plan)
            i = _inst(plan, x, t["date"])
            i["amount"] = t["amount"]
            i["tax"] = _num(m.group("tax"))
            i["txn_ids"].append(t["id"])
        elif _SBI_INTEREST.search(desc):
            interests.append(t)
        elif _SBI_FEE.search(desc):
            fees.append(t)
    for t in interests:
        same_day = [(p, i) for p in plans if p["card"] == t["card_last4"]
                    for i in p["installments"].values() if i["date"] == t["date"]]
        if len(same_day) == 1:
            _p, i = same_day[0]
            i["interest"] = round(i["interest"] + t["amount"], 2)
            i["txn_ids"].append(t["id"])
    for t in fees:
        m = _SBI_FEE.search(t["raw_description"] or "")
        cands = []
        for p in plans:
            if p["card"] != t["card_last4"] or p["fee"] is not None:
                continue
            first = min(i["date"] for i in p["installments"].values())
            gap = (date.fromisoformat(first) - date.fromisoformat(t["date"])).days
            if 0 <= gap <= 45:
                cands.append((gap, p))
        if cands:
            _gap, p = min(cands, key=lambda c: c[0])
            p["fee"] = t["amount"]
            p["fee_tax"] = _num(m.group("tax")) if m and m.group("tax") else 0.0
            p["extra_ids"].append(t["id"])
    for p in plans:
        for i in p["installments"].values():
            i["principal"] = round(i["amount"] - i["interest"], 2)
    return plans


def _icici_plans(lines):
    by_card = defaultdict(list)
    for t in lines:
        m = _ICICI_LINE.search(t["raw_description"] or "")
        if m:
            by_card[t["card_last4"]].append((t, m))
    plans = []
    for card, items in by_card.items():
        card_plans = []
        unmarked = []
        for t, m in items:
            if not m.group("x"):
                unmarked.append((t, m))
                continue
            x, n = int(m.group("x")), int(m.group("n"))
            tag = _norm(m.group("m"))[:12] or "emi"
            start = _month_add(t["date"][:7], -(x - 1))
            plan = next((p for p in card_plans if (p["tag"], p["tenure"]) == (tag, n)
                         and abs(_months_between(p["start"] + "-01", start + "-01")) <= 1), None)
            if plan is None:
                plan = _new_plan("ICICI", card, tag, n)
                plan["start"] = start
                plan["merchant"] = (m.group("m") or "").strip() or None
                card_plans.append(plan)
            _icici_put(plan, x, t, m.group("part"))
        leftovers = []
        # An unmarked line belongs to the plan that posts on its day: the instalment printed on the same
        # date, else a plan whose lines fall on the same day of the month (each plan posts on its own
        # anniversary day). Only when two plans share that day does size decide (the biggest line to the
        # plan with the most principal left). A line no plan posts on its day is another plan's
        # (leftovers): two plans at once on one card used to swap interest by size (verifier round 2).
        for t, m in sorted(unmarked, key=lambda tm: (tm[0]["date"][:7], tm[1].group("part").lower(),
                                                     -tm[0]["amount"], tm[0]["id"])):
            part = m.group("part").lower()
            month = t["date"][:7]
            cands = []
            for p in card_plans:
                k = _months_between(p["start"] + "-01", month + "-01")
                if 0 <= k < p["tenure"]:
                    i = p["installments"].get(k + 1)
                    if i is None or (part == "principal" and i["principal"] is None) or (
                            part == "interest" and not i["txn_ids_interest"]):
                        cands.append((p, k + 1))
            same_date = [c for c in cands if (c[0]["installments"].get(c[1]) or {}).get("date") == t["date"]]
            same_day = [c for c in cands
                        if any(_same_day_of_month(i["date"], t["date"]) for i in c[0]["installments"].values())]
            cands = same_date or same_day
            if not cands:
                leftovers.append((t, m))
                continue
            if len(cands) > 1:
                cands.sort(key=lambda c: -_outstanding(c[0], c[1]))
            p, x = cands[0]
            _icici_put(p, x, t, m.group("part"))
        if leftovers:
            plan = _new_plan("ICICI", card, "unmarked", None)
            plan["start"] = leftovers[0][0]["date"][:7]
            for t, m in leftovers:
                x = _months_between(plan["start"] + "-01", t["date"][:7] + "-01") + 1
                _icici_put(plan, x, t, m.group("part"))
            card_plans.append(plan)
        plans.extend(card_plans)
    for p in plans:
        for i in p["installments"].values():
            i.pop("txn_ids_interest", None)
            i["principal"] = round(i["principal"] or 0.0, 2)
            i["amount"] = round(i["principal"] + i["interest"], 2)
    return plans


def _icici_put(plan, x, t, part):
    i = _inst(plan, x, t["date"])
    i.setdefault("txn_ids_interest", [])
    if part.lower() == "principal":
        i["principal"] = round((i["principal"] or 0.0) + t["amount"], 2)
        i["date"] = t["date"]
    else:
        i["interest"] = round(i["interest"] + t["amount"], 2)
        i["txn_ids_interest"].append(t["id"])
    i["txn_ids"].append(t["id"])


def _same_day_of_month(a_iso, b_iso, slack=2):
    """Two posting dates on the same day of the month, give or take a weekend (31st ~ 1st)."""
    d = abs(int(a_iso[8:10]) - int(b_iso[8:10]))
    return min(d, 31 - d) <= slack


def _outstanding(plan, upto_x):
    return sum((i["principal"] or 0.0) for x, i in plan["installments"].items() if x >= upto_x)


def _hdfc_plans(lines):
    plans = {}
    for t in lines:
        m = _HDFC_LINE.search(t["raw_description"] or "")
        if not m:
            continue
        key = (t["card_last4"], m.group("loan").lstrip("0"))
        plan = plans.get(key)
        if plan is None:
            plan = plans[key] = _new_plan("HDFC", t["card_last4"], key[1], None)
        i = _inst(plan, int(m.group("x")), t["date"])
        if m.group("part").upper() == "PRIN":
            i["principal"] = round((i["principal"] or 0.0) + t["amount"], 2)
        else:
            i["interest"] = round(i["interest"] + t["amount"], 2)
        i["txn_ids"].append(t["id"])
    for p in plans.values():
        for i in p["installments"].values():
            i["principal"] = round(i["principal"] or 0.0, 2)
            i["amount"] = round(i["principal"] + i["interest"], 2)
    return list(plans.values())


# --- assembling ------------------------------------------------------------------------------------

BOOKING_ONLY_DAYS = 60         # a booking with no statement line yet is shown while this fresh
COVERAGE_GAP_DAYS = 45         # booking -> first line further apart: early statements are missing
LATE_LINES_MAX_DAYS = 400      # ... but still the same plan when no other plan on the card fits


def _attach_bookings(conn, plans, bookings, today):
    cards_by_bank = _card_sets(conn)
    for b in bookings:
        cards = {b["card"]} if b["card"] else cards_by_bank.get(b["bank"], set())
        day = b.get("txn_date") or b.get("booking_date")
        purchase = _purchase(conn, cards, b["amount"], day)
        card = b["card"] or (purchase["card_last4"] if purchase else None)
        b["purchase"] = purchase
        target, best = None, None
        for p in plans:
            if p["bank"] != b["bank"] or p["card"] != card or p["booking"] is not None:
                continue
            if b["tenure"] and p["tenure"] and b["tenure"] != p["tenure"]:
                continue
            first = _first_date(p)
            if not first or not day:
                continue
            gap = (date.fromisoformat(first) - date.fromisoformat(day)).days
            if -5 <= gap <= 75 and (best is None or gap < best):
                target, best = p, gap
        if target is None and not b["tenure"] and day:
            # A conversion booked before the statements that are parsed (ICICI names no tenure and its
            # early lines carry no <x/n> marker): the nearest later plan on the card without a booking.
            later = [p for p in plans if p["bank"] == b["bank"] and p["card"] == card and p["booking"] is None
                     and _first_line(p) and 0 < (date.fromisoformat(_first_line(p)) - date.fromisoformat(day)).days
                     <= LATE_LINES_MAX_DAYS]
            if later:
                target = min(later, key=lambda p: _first_line(p))
        if target is None:
            fresh = b.get("first_emi_date") and b["first_emi_date"] >= (today - timedelta(days=31)).isoformat()
            recent = day and (today - date.fromisoformat(day)).days <= BOOKING_ONLY_DAYS
            if not card or not (fresh or recent):
                continue        # an old booking whose lines are not in the parsed statements
            target = _new_plan(b["bank"], card, "booking", b["tenure"])
            plans.append(target)
        target["booking"] = b
        first_line = _first_line(target)
        if first_line and day and (date.fromisoformat(first_line) - date.fromisoformat(day)).days > COVERAGE_GAP_DAYS:
            target["coverage_gap"] = True
        if b["tenure"] and not target["tenure"]:
            target["tenure"] = b["tenure"]


def _first_line(plan):
    return min((i["date"] for i in plan["installments"].values()), default=None)


def _first_date(plan):
    if plan.get("booking") and plan["booking"].get("first_emi_date"):
        return plan["booking"]["first_emi_date"]
    if not plan["installments"] or plan.get("coverage_gap"):
        # with early statements missing, instalment numbers start at the first line seen
        return _first_line(plan)
    x, i = min(plan["installments"].items())
    return _add_months(i["date"], -(x - 1))


def _finish(plan, today):
    b = plan["booking"] or {}
    insts = [plan["installments"][x] for x in sorted(plan["installments"])]
    purchase = b.get("purchase")
    tenure = plan["tenure"]
    first = _first_date(plan)
    billed = max((i["n"] for i in insts), default=0)
    scheduled = 0
    if first and tenure:
        scheduled = sum(1 for k in range(tenure) if _add_months(first, k) <= today.isoformat())
    paid = max(billed, scheduled) if tenure else billed
    if tenure:
        paid = min(paid, tenure)
    last_date = insts[-1]["date"] if insts else None
    if tenure:
        status = "completed" if paid >= tenure else "active"
    elif last_date and (today - date.fromisoformat(last_date)).days > STALE_AFTER_DAYS:
        status = "ended"
    else:
        status = "active"
    if tenure and first:
        next_due = _add_months(first, paid) if paid < tenure else None
        end_date = _add_months(first, tenure - 1)
    else:
        next_due = _add_months(last_date, 1) if (last_date and status == "active") else None
        end_date = None
    purchase_amount = b.get("amount") or (purchase["amount"] if purchase else None)
    # Verifier round 2: "3 of 6 paid" sat beside interest / principal-left figures of only the 2
    # instalments on statements. Instalments the bank's schedule has charged but no parsed statement
    # prints yet are estimated from the plan's own rate (reducing balance, as the bank amortises) and
    # flagged, so every money figure covers the same instalments as installments_paid. Without a rate
    # nothing is estimated and figures_installments says how far the figures go.
    estimated = _estimate_unbilled(plan, insts, billed, paid, tenure, b.get("rate"), purchase_amount, first)
    figures = insts + estimated
    interest = round(sum(i["interest"] for i in figures), 2)
    tax = round(sum(i["tax"] for i in figures), 2)
    principal_paid = round(sum(i["principal"] or 0.0 for i in figures), 2)
    merchant = ((purchase["merchant"] if purchase else None) or plan.get("merchant")
                or b.get("merchant_hint") or f"Card {plan['card']} EMI")
    monthly = round(insts[-1]["amount"] + insts[-1]["tax"], 2) if insts else b.get("emi_amount")
    start_key = (first or (insts[0]["date"] if insts else b.get("txn_date") or ""))[:7]
    plan_id = re.sub(r"[^a-z0-9:-]+", "-", f"{plan['card']}:{start_key}:{tenure or 'n'}:{plan['tag']}".lower())
    txn_ids = sorted({tid for i in insts for tid in i["txn_ids"]} | set(plan["extra_ids"]))
    sources = (["statement"] if insts else []) + list(b.get("sources") or [])
    total_paid = round(sum(i["amount"] + i["tax"] for i in figures) + (plan["fee"] or 0.0) + plan["fee_tax"], 2)
    return {
        "plan_id": plan_id,
        "series_key": "plan:" + plan_id,
        "bank": plan["bank"],
        "card_last4": plan["card"],
        "merchant": merchant,
        "purchase_amount": purchase_amount,
        "purchase_date": (purchase["date"] if purchase else None) or b.get("txn_date"),
        "purchase_txn_id": purchase["id"] if purchase else None,
        "tenure_months": tenure,
        "interest_rate_pct": b.get("rate"),
        "processing_fee": b.get("fee") if b.get("fee") is not None else plan["fee"],
        "emi_amount_bank": b.get("emi_amount"),
        # unknown when the first instalments are not on any parsed statement (see statement_lines_from)
        "first_emi_date": first if (b.get("first_emi_date") or not plan.get("coverage_gap")) else None,
        "end_date": end_date if end_date else (last_date if status != "active" else None),
        "next_due": next_due if status == "active" else None,
        "installments_billed": billed,
        "installments_paid": paid,
        "estimated_installments": len(estimated),
        "figures_installments": billed + len(estimated),
        "installments_remaining": (tenure - paid) if tenure else None,
        "monthly_payment": monthly,
        "interest_paid": interest,
        "tax_paid": tax,
        "interest_and_tax_paid": round(interest + tax, 2),
        "principal_paid": principal_paid,
        # unknowable when the first instalments are not on any parsed statement
        "principal_remaining": (round(purchase_amount - principal_paid, 2)
                                if purchase_amount and not plan.get("coverage_gap") else None),
        "statement_lines_from": _first_line(plan),
        "total_paid": total_paid,
        "last_installment_date": last_date,
        "status": status,
        "installments": [{k: v for k, v in i.items() if k != "txn_ids"}
                         | {"total": round(i["amount"] + i["tax"], 2), "estimated": bool(i.get("estimated"))}
                         for i in figures],
        "txn_ids": txn_ids,
        "sources": sources,
    }


GST_ON_INTEREST = 0.18        # the tax a bank charges on EMI interest (SBI prints it: "EXCL TAX 56.03" on 311.26)


def _estimate_unbilled(plan, insts, billed, paid, tenure, rate, purchase_amount, first):
    """Instalments billed+1 .. paid, which the bank's schedule has charged but no parsed statement
    prints: each takes the reducing-balance annuity on what is left (the bank's own EMI for the rest of
    the tenure), interest = balance x rate/12, GST on interest. Returns [] when it cannot be computed
    honestly (no rate, unknown purchase amount, statements missing the first instalments)."""
    if not (tenure and rate and insts and first and purchase_amount) or paid <= billed or plan.get("coverage_gap"):
        return []
    if sorted(i["n"] for i in insts) != list(range(1, billed + 1)):
        return []                        # a gap in the billed instalments: the balance is not known
    r = rate / 1200
    balance = round(purchase_amount - sum(i["principal"] or 0.0 for i in insts), 2)
    out = []
    for x in range(billed + 1, paid + 1):
        left = tenure - x + 1
        if balance <= 0 or left <= 0:
            break
        emi = round(balance * r / (1 - (1 + r) ** -left), 2) if r else round(balance / left, 2)
        interest = round(balance * r, 2)
        principal = round(min(emi - interest, balance), 2)
        out.append({"n": x, "date": _add_months(first, x - 1), "principal": principal, "interest": interest,
                    "tax": round(interest * GST_ON_INTEREST, 2), "amount": round(principal + interest, 2),
                    "txn_ids": [], "estimated": True})
        balance = round(balance - principal, 2)
    return out


def detect_emi_plans(conn, today=None):
    """Every EMI plan the evidence shows, newest first (see module docstring)."""
    today = today or today_ist()
    lines = conn.execute(
        "SELECT id, date, amount, raw_description, card_last4, kind FROM transactions "
        "WHERE is_duplicate = 0 AND txn_type = 'debit' AND COALESCE(card_last4, '') <> '' "
        "AND (raw_description LIKE '%EMI%' OR raw_description LIKE '%Amorti%') ORDER BY date, id"
    ).fetchall()
    lines = [dict(r) for r in lines]
    plans = _sbi_plans(lines) + _icici_plans(lines) + _hdfc_plans(lines)
    _attach_bookings(conn, plans, _bookings(conn), today)
    out = [_finish(p, today) for p in plans if p["installments"] or p["booking"]]
    out.sort(key=lambda p: p["first_emi_date"] or "", reverse=True)
    out.sort(key=lambda p: p["status"] != "active")          # stable: active first, newest first
    return out
