"""
Cross-source transaction identity — iter-18 (HP3 / H3 / M4). Supersedes cross_matcher v2 (adr-005).

The same real-world purchase can reach us up to three times: a bank SMS, a bank alert email and,
weeks later, a line on the card statement PDF. Each copy is stored as its own row (an
"observation"); this module decides which rows describe the same purchase and links them, so
exactly one row per purchase has is_duplicate = 0. Every reader already filters is_duplicate = 0,
so linking is the merge.

Identity is decided ONLY from facts that describe the money movement:
    card (normalised through statement aliases), direction, amount, date, bank reference number,
and — when those alone are ambiguous — whether the merchant text corroborates.

It is never decided from the category. v2 refused to pair an SMS whose category was
"Transfer"/"Rent & Housing", and categories are guesses: ZUDIO looked like a person's name,
TRENT contains "rent", so 132 card purchases (Rs 1,29,474) were counted twice (2026-10-19 audit).

Rules, strongest first (the rule that linked a row is stored in transactions.merge_rule):
  ref        both rows carry the same bank reference (UPI ref / UTR / approval code).
             Two DIFFERENT references are proof of two different transactions.
  same-body  two SMS rows with the identical message body received within 2 minutes (one alert
             delivered twice: two sender IDs, a forwarder retry, the backup copy).
  repeat-alert  a second ALERT FAMILY for the same card/amount within 5 minutes whose merchant
             corroborates: a spend alert + an e-mandate / standing-instruction alert, or two sender
             families. Two bodies of one template are never copies of each other unless identical —
             they differ exactly in the time, reference or balance that tells two purchases apart.
             Both SMS rules also follow a copy that is already linked to its statement line, so a
             second delivery of a billed SMS is linked too.
  twin-alert one account transfer reported by two SMS sender families (SBI YONO + core banking).
  unique     exactly one candidate on each side: same card, same amount, same direction,
             within +/-1 day. Two distinct identical purchases on one card in one day would show
             up as two statement lines, so a 1:1 pairing is the same purchase — unless both rows
             name a KNOWN brand and the brands differ with nothing tying them together (iteration
             203 E11: a Swiggy order that never posted and an Uber ride of the same amount the next
             day). A bank's registered / legal name is not a known brand, so it still merges.
  merchant   several candidates; pick the one whose merchant text corroborates.
  count      k identical rows from one source and k from another on one card and day (two Rs 4,259
             purchases 35 s apart: two SMS, two statement lines) — paired 1:1 in order.
  tolerance  amounts differ slightly (fuel surcharge, foreign-currency estimate) — only with the
             same card AND merchant corroboration.

Between two SMS rows, the facts printed in the bodies veto any link: a different transaction time,
bank reference, available balance/limit or counterparty VPA means two transactions, whatever the
stored columns say (legacy rows have no stored ref_no).

Which copy wins: statement line (card) > SMS > email. The winner keeps is_duplicate = 0 and
inherits useful fields the loser had (better merchant, reference, timestamp, original currency).
Two rows from the SAME source are never merged except by `ref` or `same-body` — that is what
keeps genuine repeat purchases (5 x Rs 280 to the same payee) as five rows.
"""
import re
from datetime import date as _date

from db import get_db
from util.timez import parse_stored_timestamp

SOURCE_PRIORITY = {"card": 3, "sms": 2, "email": 1}
DATE_WINDOW_DAYS = 1
REF_WINDOW_DAYS = 3
TOLERANCE_PCT = 1.5          # surcharge / rounding between SMS and statement
TOLERANCE_MIN_RS = 2.0
FX_TOLERANCE_PCT = 6.0       # SMS amount converted at an estimated rate vs the statement's INR

TRANSFER_LIKE = {
    "Transfer", "Self Transfer", "Family Remittance", "Housemate Expenses",
    "Home Construction", "Investment", "Rent & Housing", "ATM Withdrawal",
}

_GATEWAY_PREFIX = re.compile(
    r"^\s*(?:raz\*|razorpay\*?|pyu\*|payu\*|ptm\*|paytm\*|cashfree\*|ppsl\*|bd\*|ccavenue\*|"
    r"ind\*|msw\*|cybs\s*si\s*|cas)\s*", re.I)
_NOISE_TOKENS = {
    "ind", "india", "bangalore", "bengaluru", "mumbai", "hyderabad", "gurgaon", "gurugram",
    "delhi", "chennai", "pune", "kar", "pvt", "ltd", "limited", "private", "the", "and", "com",
    "www", "upi", "pos", "ecom", "online", "payment", "payments", "via", "credit", "card",
}


def _tokens(*texts):
    out = set()
    for t in texts:
        s = _GATEWAY_PREFIX.sub("", (t or "").lower())
        s = re.sub(r"[^a-z0-9]+", " ", s)
        out |= {w for w in s.split() if len(w) > 2 and w not in _NOISE_TOKENS and not w.isdigit()}
    return out


def _compact(t):
    return re.sub(r"[^a-z0-9]", "", _GATEWAY_PREFIX.sub("", (t or "").lower()))


# Labels the categoriser gives to any transfer; two rows sharing one say nothing about the payee.
# ('Self Transfer' is not one of them: on both rows it means the payee is the owner.)
_GENERIC_MERCHANTS = {"bank transfer", "account debit", "own account transfer",
                      "card bill payment", "internet banking transfer", "transfer", "unknown"}


def _specific(merchant):
    return None if (merchant or "").strip().lower() in _GENERIC_MERCHANTS else merchant


def merchants_corroborate(a, b) -> bool:
    """Do two rows' merchant texts plausibly name the same merchant?"""
    a = {"merchant": _specific(a["merchant"]), "raw_description": a["raw_description"]}
    b = {"merchant": _specific(b["merchant"]), "raw_description": b["raw_description"]}
    ta = _tokens(a["merchant"], a["raw_description"])
    tb = _tokens(b["merchant"], b["raw_description"])
    if ta & tb:
        return True
    # prefix match on long tokens: "swiggyinstam" vs "swiggy", "ctrlxtechnol" vs "ctrlx"
    for x in ta:
        for y in tb:
            if len(x) >= 5 and len(y) >= 5 and (x.startswith(y) or y.startswith(x)):
                return True
    for fa in (a["merchant"], a["raw_description"]):
        for fb in (b["merchant"], b["raw_description"]):
            ca, cb = _compact(fa), _compact(fb)
            if len(ca) >= 5 and len(cb) >= 5 and (ca in cb or cb in ca):
                return True
    return False


def _brand(description):
    """The brand a description prints, or None: the alias table must name it AND the text must carry
    the brand's own name ("PPSL*SwiggyBangalore" -> swiggy). A legal name or a registered address the
    alias table maps elsewhere ("3rd Floor Internally desi..." -> an owner's 'Office Rent' alias, on
    the bank's SMS for a Swiggy order) prints no brand, so it can never veto a merge."""
    from modules.categorizer import categorize, _GATEWAY_CATEGORY
    merchant, category, confidence = categorize(description or "", context="card")
    if confidence != "high" or category == _GATEWAY_CATEGORY or not _specific(merchant):
        return None
    name = _compact(merchant)
    return merchant.strip().lower() if len(name) >= 3 and name in _compact(description) else None


def merchants_disagree(x, y):
    """Both rows name a known brand, the brands differ, and nothing in the rows ties them together:
    no merchant-text overlap and no transaction time both print. (iteration 203 E11)"""
    bx, by = _brand(x["raw_description"]), _brand(y["raw_description"])
    if not bx or not by or bx == by or merchants_corroborate(x, y):
        return False
    tx, ty = _body_facts(x["raw_text"])["time"], _body_facts(y["raw_text"])["time"]
    return not (tx and ty and abs(_seconds(tx) - _seconds(ty)) <= CROSS_SOURCE_TIME_SLACK_S)


def _card_alias_map(conn):
    m = {}
    for r in conn.execute("SELECT last4, pdf_card_last4 FROM cards WHERE pdf_card_last4 IS NOT NULL"):
        m[r["pdf_card_last4"]] = r["last4"]
    return m


def _norm_card(alias, last4):
    last4 = (last4 or "").strip()
    return alias.get(last4, last4)


def _days_apart(d1, d2):
    try:
        return abs((_date.fromisoformat(d1[:10]) - _date.fromisoformat(d2[:10])).days)
    except (TypeError, ValueError):
        return 999


def _norm_body(t):
    return re.sub(r"\s+", " ", (t or "")).strip().lower()


def _template(t):
    """The alert's wording with every number masked: two bodies of one template differ only in the
    time / reference / balance / amount they report."""
    return re.sub(r"\d+", "#", _norm_body(t))


# "On 2025-11-23:22:36:22" — the date and time are joined by a colon, so only digits may not
# precede the time; a following ":NN" means we started inside a longer run ("23:22:36" of it).
_TIME_RE = re.compile(r"(?<!\d)(\d{1,2}:\d{2}:\d{2})(?![\d:])")
_BAL_RE = re.compile(
    r"\b(?:avl\.?|available)\s*(?:bal(?:ance)?|lmt|limit)\b[^\d]{0,12}([\d,]+(?:\.\d+)?)|"
    r"\bbal(?:ance)?\b[\s.:]*(?:is\s*)?(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d+)?)", re.I)
_VPA_RE = re.compile(r"\b[\w.\-]{2,}@[a-z][a-z]{1,}\b", re.I)
_MANDATE_RE = re.compile(r"e-?\s?mandate|standing\s+instruction|\bsi\s*hub\b|auto-?\s?pay|auto-?\s?debit", re.I)


def _body_facts(text):
    """What a bank alert says about the one transaction it reports, read from the text itself."""
    from modules.sms.parser import extract_ref_no
    t = text or ""
    tm = _TIME_RE.search(t)
    bal = _BAL_RE.search(t)
    bal_v = None
    if bal:
        try:
            bal_v = round(float((bal.group(1) or bal.group(2)).replace(",", "")), 2)
        except ValueError:
            bal_v = None
    time_v = None
    if tm:
        h, m, s = tm.group(1).split(":")
        time_v = f"{int(h):02d}:{m}:{s}"
    return {
        "time": time_v,
        "ref": (extract_ref_no(t) or "").upper() or None,
        "bal": bal_v,
        "vpa": {v.lower() for v in _VPA_RE.findall(t)},
    }


CROSS_SOURCE_TIME_SLACK_S = 5       # an SMS and the bank's email for one spend print the same time to
                                    # within 2 s (503 real pairs); two purchases 35 s apart must not cross


def _seconds(hms):
    h, m, s = (int(v) for v in hms.split(":"))
    return h * 3600 + m * 60 + s


def _facts_conflict(a_text, b_text, cross_source=False):
    """Do two alert bodies describe two different transactions? Any fact both print and that
    differs is proof: a second purchase 35 s later has its own auth time; a second UPI payment its
    own reference; a second debit leaves a different balance; a credit from another VPA is another
    credit.

    Between an SMS and an email (cross_source) the formats differ, so only the balance and the time
    of day are compared, the time with CROSS_SOURCE_TIME_SLACK_S of slack (the two are stamped
    separately); references are compared from the stored ref_no instead."""
    fa, fb = _body_facts(a_text), _body_facts(b_text)
    if fa["bal"] is not None and fb["bal"] is not None and fa["bal"] != fb["bal"]:
        return True
    if fa["time"] is not None and fb["time"] is not None:
        gap = abs(_seconds(fa["time"]) - _seconds(fb["time"]))
        gap = min(gap, 86400 - gap)
        if gap > (CROSS_SOURCE_TIME_SLACK_S if cross_source else 0):
            return True
    if cross_source:
        return False
    if fa["ref"] is not None and fb["ref"] is not None and fa["ref"] != fb["ref"]:
        return True
    return bool(fa["vpa"] and fb["vpa"] and not (fa["vpa"] & fb["vpa"]))


def _ref_of(row):
    """The row's bank reference: the stored one, else the one its own text prints."""
    ref = (row["ref_no"] or "").strip().upper()
    if ref or row["source"] != "sms":
        return ref
    from modules.sms.parser import extract_ref_no
    return (extract_ref_no(row["raw_text"] or "") or "").upper()


TWIN_MIN_RS = 1000.0


def _sender_family(conn, row):
    """'AD-SBYONO-S' -> 'SBYONO': the SMS header without the operator prefix and -S/-T suffix."""
    if row["source"] != "sms" or not row["source_ref"]:
        return None
    r = conn.execute("SELECT sender FROM sms WHERE id = ?", (row["source_ref"],)).fetchone()
    if not r or not r["sender"]:
        return None
    s = r["sender"].upper()
    s = re.sub(r"^[A-Z]{2}-", "", s)
    s = re.sub(r"-[A-Z]$", "", s)
    return s


_ROW_COLS = ("id, source, source_ref, amount, date, card_last4, txn_type, merchant, raw_description, category, "
             "confidence, ref_no, raw_text, currency, original_amount, fx_estimated, occurred_at, "
             "is_duplicate, matched_txn_id, merge_rule")


def _row(conn, txn_id):
    return conn.execute(f"SELECT {_ROW_COLS} FROM transactions WHERE id = ?", (txn_id,)).fetchone()


def _claimed_by_source(conn, winner_id, source):
    """Has `winner_id` already absorbed a duplicate from `source`? (1:1 pairing per source)"""
    return conn.execute(
        "SELECT 1 FROM transactions WHERE matched_txn_id = ? AND source = ? LIMIT 1",
        (winner_id, source),
    ).fetchone() is not None


def _amount_close(a, b):
    """'exact' | 'tolerance' | None."""
    x, y = float(a["amount"]), float(b["amount"])
    if abs(x - y) < 0.005:
        return "exact"
    hi = max(x, y)
    if a["fx_estimated"] or b["fx_estimated"]:
        if abs(x - y) <= hi * FX_TOLERANCE_PCT / 100:
            return "tolerance"
        return None
    if abs(x - y) <= max(TOLERANCE_MIN_RS, hi * TOLERANCE_PCT / 100):
        return "tolerance"
    return None


def _candidates(conn, alias, x, window):
    """Rows that could be the same transaction as `x` (any source, other row)."""
    lo_hi = (x["date"], x["date"])
    rows = conn.execute(
        f"SELECT {_ROW_COLS} FROM transactions "
        "WHERE id <> ? AND is_duplicate = 0 AND txn_type = ? "
        "AND date BETWEEN date(?, ?) AND date(?, ?)",
        (x["id"], x["txn_type"], lo_hi[0], f"-{window} day", lo_hi[1], f"+{window} day"),
    ).fetchall()
    xc = _norm_card(alias, x["card_last4"])
    out = []
    for y in rows:
        yc = _norm_card(alias, y["card_last4"])
        if xc and yc and xc != yc:
            continue
        out.append((y, yc))
    return xc, out


SAME_BODY_MAX_SECONDS = 120      # one alert delivered twice arrives within seconds (two sender IDs,
                                 # forwarder retry + backup copy: 0.02-2 s apart in the live data)
REPEAT_ALERT_MAX_SECONDS = 300   # a bank's second wording for the same purchase (spend alert +
                                 # e-mandate / standing-instruction alert): 19-62 s apart in the data


def _sms_time(conn, row):
    """When the phone received an SMS-sourced row (aware UTC datetime), or None."""
    if row["source"] != "sms" or not row["source_ref"]:
        return None
    r = conn.execute("SELECT phone_timestamp, received_at FROM sms WHERE id = ?", (row["source_ref"],)).fetchone()
    if not r:
        return None
    return parse_stored_timestamp(r["phone_timestamp"]) or parse_stored_timestamp(r["received_at"])


def _group(conn, row):
    """`row` and every duplicate already linked to it (for a duplicate: its winner's group)."""
    head = row
    if row["is_duplicate"] and row["matched_txn_id"]:
        head = _row(conn, row["matched_txn_id"]) or row
    return [head] + conn.execute(
        f"SELECT {_ROW_COLS} FROM transactions WHERE matched_txn_id = ? AND is_duplicate = 1",
        (head["id"],)).fetchall()


def _family_of(conn, row):
    """Who sent an alert: the SMS sender family, or the alert email's sender address."""
    if row["source"] == "sms":
        return _sender_family(conn, row)
    if row["source"] == "email" and row["source_ref"]:
        r = conn.execute("SELECT sender_email FROM emails WHERE id = ?", (row["source_ref"],)).fetchone()
        return ("email", (r["sender_email"] or "").lower()) if r else None
    return None


def _alert_text(conn, row):
    """The full text a bank alert printed: the SMS body, or the alert email's subject + body (the
    row's raw_text keeps only a 250-character excerpt, which can stop before the balance)."""
    if row["source"] == "email" and row["source_ref"]:
        r = conn.execute("SELECT subject, body_text, body_snippet FROM emails WHERE id = ?",
                         (row["source_ref"],)).fetchone()
        if r:
            return f"{r['subject'] or ''} | {r['body_text'] or r['body_snippet'] or ''}"
    return row["raw_text"] or ""


def _two_transactions(conn, a, b, text):
    """Do rows a and b (copies in one group, or about to be) describe two different transactions?"""
    a_ref, b_ref = _ref_of(a), _ref_of(b)
    if a_ref and b_ref and a_ref != b_ref:
        return True
    if a["source"] not in ("sms", "email") or b["source"] not in ("sms", "email"):
        return False
    if _facts_conflict(text(a), text(b), cross_source=a["source"] != b["source"]):
        return True
    a_fam = _family_of(conn, a)
    return bool(a_fam and a["source"] == b["source"] and _family_of(conn, b) == a_fam
                and _template(text(b)) == _template(text(a)) and _norm_body(text(b)) != _norm_body(text(a)))


def _text_cache(conn):
    texts = {}

    def text(r):
        if r["id"] not in texts:
            texts[r["id"]] = _alert_text(conn, r)
        return texts[r["id"]]
    return text


def groups_compatible(conn, x, y):
    """Can the transaction `x` stands for be the same one `y` stands for?

    Every row of x's group is checked against every row of y's group, not only the two rows in hand:
    a second same-day Rs 50,000 transfer reported only by core banking was absorbed into the first
    transfer's group by the hourly sweep, because the first transfer's winner (a YONO alert, which
    prints no balance) agreed with it while the first transfer's own core-banking alert did not
    (iter-18 verifier). Any two rows with different references, conflicting printed facts (balance,
    time; see _facts_conflict), or two different bodies of one template from one sender (a bank
    reports one transaction once per template; an identical re-delivery is `same-body`) say: two
    transactions."""
    gx, gy = _group(conn, x), _group(conn, y)
    ids_x = {r["id"] for r in gx}
    text = _text_cache(conn)
    return not any(_two_transactions(conn, a, b, text) for a in gx for b in gy if b["id"] not in ids_x)


def repair_inconsistent_groups(conn=None, dry_run=True):
    """Undo links whose group holds two different transactions, so the sweep can re-make them.

    The pre-iter-18 matcher linked by amount and date alone: card 9320's Rs 49 statement line of
    12 Apr held the SMS of 11 Apr (08:58:13) AND of 12 Apr (06:57:19), while the 11 Apr line held the
    12 Apr email — every later copy then had no consistent group to join (iter-18 verifier). For each
    group whose own members conflict, every SMS / email copy linked to a winner with the same card,
    amount and direction within +/-1 day of it is detached (made live again); the sweep that follows
    re-links them under today's rules. Idempotent: consistent groups are never touched."""
    conn = conn or get_db()
    alias = _card_alias_map(conn)
    text = _text_cache(conn)
    winners = conn.execute(
        f"SELECT {_ROW_COLS} FROM transactions WHERE is_duplicate = 0 AND id IN "
        "(SELECT matched_txn_id FROM transactions WHERE is_duplicate = 1 AND source IN ('sms', 'email'))").fetchall()
    bad = []
    for w in winners:
        members = _group(conn, w)
        if any(_two_transactions(conn, a, b, text) for i, a in enumerate(members) for b in members[i + 1:]):
            bad.append(w)
    detach = {}
    for w in bad:
        wc = _norm_card(alias, w["card_last4"])
        for v in conn.execute(
                f"SELECT {_ROW_COLS} FROM transactions WHERE is_duplicate = 0 AND txn_type = ? "
                "AND ABS(amount - ?) < 0.005 AND date BETWEEN date(?, '-1 day') AND date(?, '+1 day')",
                (w["txn_type"], w["amount"], w["date"], w["date"])).fetchall():
            if _norm_card(alias, v["card_last4"]) != wc:
                continue
            for d in conn.execute("SELECT id FROM transactions WHERE matched_txn_id = ? AND is_duplicate = 1 "
                                  "AND source IN ('sms', 'email')", (v["id"],)).fetchall():
                detach[d["id"]] = v["id"]
    if not dry_run and detach:
        conn.executemany("UPDATE transactions SET is_duplicate = 0, matched_txn_id = NULL, merge_rule = NULL WHERE id = ?",
                         [(i,) for i in detach])
        conn.commit()
    return {"dry_run": dry_run, "inconsistent_groups": len(bad), "detached": len(detach),
            "groups": [w["id"] for w in bad][:20]}


def _second_alert_family(conn, x, y):
    """Are x and y two different KINDS of alert (not two bodies of one template)? A spend alert
    plus the e-mandate / standing-instruction alert for the same charge, or two sender families
    (SBI YONO + core banking). Two bodies of one template that are not identical report two
    transactions: HDFC 'Spent Rs.4259 ... 22:36:22' and '... 22:36:57' were two purchases with two
    statement lines (iter-18 verifier, 6 wrong links of 9)."""
    if _template(x["raw_text"]) == _template(y["raw_text"]):
        return False
    if bool(_MANDATE_RE.search(x["raw_text"] or "")) != bool(_MANDATE_RE.search(y["raw_text"] or "")):
        return True
    xs, ys = _sender_family(conn, x), _sender_family(conn, y)
    return bool(xs and ys and xs != ys)


def _sms_sibling(conn, alias, x, xc, x_ref):
    """Another SMS row that reports the SAME purchase as `x`, resolved to the row it should link to.

    same-body     identical text received within SAME_BODY_MAX_SECONDS — one alert delivered twice.
                  Identical text minutes apart is NOT a copy: SBI's card alert carries no time or
                  reference, so two Rs 306 Blinkit orders five minutes apart read the same (iter-18 M4).
    repeat-alert  a second alert family (see _second_alert_family) for the same card, amount and
                  direction within REPEAT_ALERT_MAX_SECONDS whose merchant corroborates and whose
                  printed facts do not conflict (SBI "Rs.399.00 spent ... at OPENAI" + "Transaction
                  of Rs.399.00 at OPENAI against E-mandate" 62 s later).

    The sibling may already be linked to a statement line; then `x` links to that same winner —
    otherwise a second delivery of an already-billed SMS stays live and is counted twice (iter-18
    verifier: 7 such double counts after the history reprocess)."""
    xt = _sms_time(conn, x)
    if xt is None or not xc:
        return None, None
    rows = conn.execute(
        f"SELECT {_ROW_COLS} FROM transactions WHERE source = 'sms' AND id <> ? AND txn_type = ? "
        "AND ABS(amount - ?) < 0.005 AND date BETWEEN date(?, '-1 day') AND date(?, '+1 day')",
        (x["id"], x["txn_type"], x["amount"], x["date"], x["date"]),
    ).fetchall()
    xb = _norm_body(x["raw_text"])
    x_ref = _ref_of(x)
    # nearest in time first: of two transfers' alerts minutes apart, the closer one is x's sibling
    rows = sorted(rows, key=lambda y: (_gap_s(conn, x, y), y["id"]))
    for y in rows:
        if _norm_card(alias, y["card_last4"]) != xc:
            continue
        y_ref = _ref_of(y)
        if x_ref and y_ref and x_ref != y_ref:
            continue
        yt = _sms_time(conn, y)
        if yt is None:
            continue
        gap = abs((xt - yt).total_seconds())
        same_text = bool(xb) and _norm_body(y["raw_text"]) == xb
        if same_text and gap <= SAME_BODY_MAX_SECONDS:
            rule = "same-body"
        elif (not same_text and gap <= REPEAT_ALERT_MAX_SECONDS and merchants_corroborate(x, y)
              and not _facts_conflict(x["raw_text"], y["raw_text"]) and _second_alert_family(conn, x, y)):
            rule = "repeat-alert"
        else:
            continue
        if y["is_duplicate"] and y["matched_txn_id"]:
            if y["matched_txn_id"] == x["id"]:
                continue                       # y is already x's copy: nothing left to link
            winner = _row(conn, y["matched_txn_id"])
            if (winner is not None and not winner["is_duplicate"] and winner["id"] != x["id"]
                    and groups_compatible(conn, x, winner)):
                return winner, rule
            continue
        if groups_compatible(conn, x, y):
            return y, rule
    return None, None


def _order_key(conn, row):
    t = _sms_time(conn, row)
    return (t.isoformat() if t else (row["occurred_at"] or ""), row["id"])


def _count_pair(conn, alias, x, xc, exact):
    """Identical purchases (same card, amount, direction) that no rule can tell apart, paired 1:1
    in time order — two Rs 4,259 purchases 35 s apart on card 9434 arrived as two SMS, two emails
    and two statement lines.

    Against statement lines (a statement lists every purchase of its cycle), k SMS / email rows
    pair with k of n >= k lines within +/-1 day: every alert is one of those lines, so the live
    count can never drop below the statement's own. This also holds while lines arrive one by one
    and when a line posts the next day. Between SMS and email (neither is complete) the counts
    must be equal and on one day. Any other shape is left alone."""
    if not xc:
        return None
    sources = {y["source"] for y in exact}
    if len(sources) != 1:
        return None
    other = next(iter(sources))
    if "card" in (x["source"], other):
        low_src = other if x["source"] == "card" else x["source"]
        days = (f"-{DATE_WINDOW_DAYS} day", f"+{DATE_WINDOW_DAYS} day")
    else:
        if any(y["date"] != x["date"] for y in exact):
            return None
        low_src, days = None, ("+0 day", "+0 day")
    peers = [
        p for p in conn.execute(
            f"SELECT {_ROW_COLS} FROM transactions WHERE is_duplicate = 0 AND source = ? AND txn_type = ? "
            "AND date BETWEEN date(?, ?) AND date(?, ?) AND ABS(amount - ?) < 0.005",
            (x["source"], x["txn_type"], x["date"], days[0], x["date"], days[1], x["amount"]),
        ).fetchall()
        if _norm_card(alias, p["card_last4"]) == xc and not _claimed_by_source(conn, p["id"], other)
    ]
    if x["id"] not in {p["id"] for p in peers}:
        return None
    peers.sort(key=lambda r: (r["date"], _order_key(conn, r)))
    ys = sorted(exact, key=lambda r: (r["date"], _order_key(conn, r)))
    if low_src is None:
        if len(peers) != len(ys):
            return None
    else:
        alerts, lines = (peers, ys) if x["source"] == low_src else (ys, peers)
        if len(alerts) > len(lines):
            return None              # more alerts than lines: a copy not yet merged, or not billed yet
    i = [p["id"] for p in peers].index(x["id"])
    return ys[i] if i < len(ys) else None


def ref_match(conn, x, alias=None):
    """The live row carrying x's bank reference (+/-3 days, same direction, amount close), or None.

    A long reference (UTR / RRN, 10+ characters) is decisive even when the two copies name the
    account differently: one bank's UPI-ATM SMS prints one number for the account and its alert
    email another, for the same withdrawal and RRN (Rs 9,100 counted twice, iter-18 verifier). A
    reference can also have been inherited by a group's winner from a copy, so the groups must still
    be compatible (printed balance / time)."""
    alias = alias if alias is not None else _card_alias_map(conn)
    x_ref = (x["ref_no"] or "").strip()
    if not x_ref:
        return None
    xc = _norm_card(alias, x["card_last4"])
    for y in conn.execute(
            f"SELECT {_ROW_COLS} FROM transactions WHERE id <> ? AND is_duplicate = 0 AND txn_type = ? "
            "AND UPPER(TRIM(ref_no)) = ? AND date BETWEEN date(?, ?) AND date(?, ?)",
            (x["id"], x["txn_type"], x_ref.upper(), x["date"], f"-{REF_WINDOW_DAYS} day",
             x["date"], f"+{REF_WINDOW_DAYS} day")).fetchall():
        yc = _norm_card(alias, y["card_last4"])
        if xc and yc and xc != yc and len(x_ref) < 10:
            continue
        if _amount_close(x, y) and groups_compatible(conn, x, y):
            return y
    return None


TWIN_MAX_SECONDS = 600           # YONO + core-banking alerts for one transfer: 19-127 s apart in the data
TWIN_UNCORROBORATED_SECONDS = 180
_NOT_NAMES = {"mr", "mrs", "ms", "shri", "smt", "transfer", "transferred", "to", "from", "the", "and", "for",
              "account", "bank", "debited", "credited", "fund", "self", "upi", "imps", "neft", "rtgs"}


def _names(text):
    return {w for w in re.findall(r"[a-z]+", (text or "").lower()) if len(w) >= 3 and w not in _NOT_NAMES}


def _payees_conflict(x, y):
    """Both alerts name a payee and the names share nothing ('Ramesh' vs 'Mr. SURESH BABU'): two
    transfers. One side naming only an account ('to . Ac X9999') is no conflict."""
    nx, ny = _names(x["raw_description"]), _names(y["raw_description"])
    return bool(nx and ny and not merchants_corroborate(x, y))


def _when(conn, row):
    """When a row's own source says it happened / was sent (aware UTC), or None."""
    t = _sms_time(conn, row)
    if t is not None:
        return t
    if row["source"] == "email" and row["source_ref"]:
        r = conn.execute("SELECT date FROM emails WHERE id = ?", (row["source_ref"],)).fetchone()
        if r and r["date"]:
            try:
                from email.utils import parsedate_to_datetime
                from datetime import timezone as _tz
                d = parsedate_to_datetime(r["date"])
                return (d if d.tzinfo else d.replace(tzinfo=_tz.utc)).astimezone(_tz.utc)
            except (TypeError, ValueError, IndexError):
                pass
    return parse_stored_timestamp(row["occurred_at"])


def _gap_s(conn, a, b):
    ta, tb = _when(conn, a), _when(conn, b)
    return abs((ta - tb).total_seconds()) if ta is not None and tb is not None else float("inf")


# --- far SMS <-> email pairings (iteration-198 re-verify, 2026-10-20) ---------------------------------
# An SMS and the bank's email for one movement are usually 1-2 s apart, but not always: HDFC card mails
# arrive hours late, ICICI reversal mails ~8 h, an SBI standing-instruction mail 40 min BEFORE its SMS
# (41 of 1,205 linked pairs on the live data are more than 10 min apart, all genuine). So distance alone
# never refuses a pairing. What does: a NEARER alert that provably belongs to another movement. Two
# Rs 50,000 transfers 30 min apart, T1's YONO mail and T2's core-banking SMS: T1's own core-banking mail
# sits 20 s from the YONO mail and prints a balance T2's SMS contradicts — the far pair is a guess
# against evidence (the verifier's probe: 15 of 23 double counts came from such `unique` / `merchant`
# links, 4 of them on alert sets seen in the live data).
CROSS_ALERT_MAX_SECONDS = 600
WEAK_RULES = ("unique", "merchant")


def _alert_neighbours(conn, alias, x):
    """SMS / email rows (live or not) on x's card, same direction and exact amount, within +/-1 day."""
    xc = _norm_card(alias, x["card_last4"])
    rows = conn.execute(
        f"SELECT {_ROW_COLS} FROM transactions WHERE id <> ? AND source IN ('sms', 'email') AND txn_type = ? "
        "AND ABS(amount - ?) < 0.005 AND date BETWEEN date(?, '-1 day') AND date(?, '+1 day')",
        (x["id"], x["txn_type"], x["amount"], x["date"], x["date"])).fetchall()
    return [r for r in rows if xc and _norm_card(alias, r["card_last4"]) == xc]


def far_against_nearer(conn, alias, x, y, text=None):
    """x and y (an SMS and an email) are more than CROSS_ALERT_MAX_SECONDS apart and an alert outside
    both groups sits within CROSS_ALERT_MAX_SECONDS of one of them while provably being another
    transaction than the other side (different reference / balance / printed time / payee)."""
    if {x["source"], y["source"]} != {"sms", "email"}:
        return False
    gap = _gap_s(conn, x, y)
    if gap == float("inf") or gap <= CROSS_ALERT_MAX_SECONDS:
        return False
    text = text or _text_cache(conn)
    gx, gy = _group(conn, x), _group(conn, y)
    if _confirmed(text, gx, gy):
        return False                      # they print the same time / balance: proof beats distance
    inside = {r["id"] for r in gx} | {r["id"] for r in gy}
    for z in _alert_neighbours(conn, alias, x):
        if z["id"] in inside:
            continue
        if _gap_s(conn, z, x) <= CROSS_ALERT_MAX_SECONDS and _against(conn, z, gy, text):
            return True
        if _gap_s(conn, z, y) <= CROSS_ALERT_MAX_SECONDS and _against(conn, z, gx, text):
            return True
    return False


def proof_target(conn, x, alias=None):
    """The live group whose alerts print x's own balance or time of day (proof), compatible with x and
    holding no alert of x's own sender family; the nearest in time if several. None otherwise.

    find_match pairs 1:1 per source (one email per SMS group), which is right for guesses but wrong
    for proof: a YONO mail and a core-banking mail both belong to their transfer's SMS group. Round 9:
    T2's core-banking mail printed T2's core-banking SMS's balance but that group already held T2's YONO
    mail, so it was left to pair as a 'twin' with T1's mail 110 s away (a double); and two same-amount
    card mails processed after their statement lines both stayed live because `unique` saw two live
    mails on each line."""
    alias = alias if alias is not None else _card_alias_map(conn)
    if x["is_duplicate"]:
        return None
    xc = _norm_card(alias, x["card_last4"])
    if not xc:
        return None
    text = _text_cache(conn)
    gx = _group(conn, x)
    mine = {r["id"] for r in gx}
    fam = (x["source"], _family_of(conn, x))
    best, seen = None, set()
    for r in conn.execute(
            f"SELECT {_ROW_COLS} FROM transactions WHERE id <> ? AND txn_type = ? AND ABS(amount - ?) < 0.005 "
            "AND date BETWEEN date(?, '-1 day') AND date(?, '+1 day')",
            (x["id"], x["txn_type"], x["amount"], x["date"], x["date"])).fetchall():
        if r["id"] in mine or _norm_card(alias, r["card_last4"]) != xc:
            continue
        head = _row(conn, r["matched_txn_id"]) if r["is_duplicate"] and r["matched_txn_id"] else r
        if head is None or head["id"] in seen or head["id"] in mine or head["is_duplicate"]:
            continue
        seen.add(head["id"])
        group = _group(conn, head)
        if any((m["source"], _family_of(conn, m)) == fam for m in group):
            continue                        # one alert per sender family and source in a group
        # proof from a member that only sits there as a far guess is no proof of the group
        if not _confirmed(text, gx, [m for m in group if not _far_copy(conn, m)]):
            continue
        if not groups_compatible(conn, x, head):
            continue
        gap = min(_gap_s(conn, x, m) for m in group)
        if best is None or gap < best[0]:
            best = (gap, head)
    return best[1] if best else None


def match_with_proof(conn, x, alias=None):
    """find_match, and when it finds nothing, the group that prints x's own balance / time (rule 'proof')."""
    y, rule = find_match(conn, x, alias)
    if y is None:
        y = proof_target(conn, x, alias)
        rule = "proof" if y is not None else None
    return y, rule


def _match_releasing(conn, x, alias):
    """find_match; if nothing, release the far guesses in x's way and try again; then proof. Release
    comes before proof: a group 'proven' through a far-guessed copy would swallow x (T2's core-banking
    SMS joining T1's YONO SMS through T2's mail that sat there as a 30-min guess: a loss).
    Returns (row, rule, released ids)."""
    y, rule = find_match(conn, x, alias)
    released = []
    if y is None:
        released = release_far_copies(conn, x, alias)
        if released:
            y, rule = find_match(conn, x, alias)
    if y is None:
        y = proof_target(conn, x, alias)
        rule = "proof" if y is not None else None
    return y, rule, released


def facts_confirm(conn, x, y):
    """Some row of x's group and some row of y's group print the SAME balance, or the same time of day
    (within CROSS_SOURCE_TIME_SLACK_S): positive proof they report one movement, stronger than any
    'same amount, different template, minutes apart' guess."""
    return _confirmed(_text_cache(conn), _group(conn, x), _group(conn, y))


def _confirmed(text, rows_a, rows_b):
    for a in rows_a:
        fa = _body_facts(text(a))
        for b in rows_b:
            if a["id"] == b["id"]:
                continue
            fb = _body_facts(text(b))
            if fa["bal"] is not None and fa["bal"] == fb["bal"]:
                return True
            if fa["time"] and fb["time"]:
                d = abs(_seconds(fa["time"]) - _seconds(fb["time"]))
                if min(d, 86400 - d) <= CROSS_SOURCE_TIME_SLACK_S:
                    return True
    return False


def _against(conn, z, group, text):
    """Is alert z provably another transaction than some row of `group`?"""
    return any(_two_transactions(conn, z, m, text) or transfer_payees_conflict(z, m) for m in group)


def transfer_payees_conflict(a, b):
    """Two account-transfer alerts naming different beneficiaries ('Asha Rani' vs 'Mr. RAVI KUMAR').

    Only a beneficiary the bank's TRANSFER wording names counts ("for transfer to <name> Ac",
    "-Transferred to Mr. <NAME>.", "Beneficiary Name <name> Beneficiary"). A card spend's SMS and email
    name the merchant differently all the time (a registered address vs 'PPSL*Swiggy'), and a narration
    or the parser's fallback is no name: 'towards annual maintenance charges' vs 'SBI ACCOUNT
    TRANSACTION' kept a bank charge's SMS and mail apart (round 9)."""
    na, nb = beneficiary(a), beneficiary(b)
    if not na or not nb:
        return False
    return not merchants_corroborate({"merchant": na, "raw_description": na}, {"merchant": nb, "raw_description": nb})


_BENEFICIARY_RES = (
    # YONO SMS "for transfer to Asha Ac Xx7777", core banking "-Transferred to Mrs. ASHA RANI. Avl Balance"
    re.compile(r"\btransfer(?:red)?\s+to\s+(?:(?:mr|mrs|ms|m/s|shri|smt)\.?\s+)?([a-z][a-z .]*?)"
               r"(?=\s*(?:\.|,|-|\bac\b|\ba/c\b|\bdt\b|\bon\b|\bref\b|\bavl\b|$))", re.I),
    # YONO mail "Beneficiary Name Asha Rani Beneficiary Account Number", NEFT "Beneficiary Name: X Beneficiary A/c"
    re.compile(r"\bbeneficiary name:?\s+([a-z][a-z .]*?)(?=\s+beneficiary\b|\s*$)", re.I),
)


def beneficiary(row):
    """The beneficiary a transfer alert names, or None (not a transfer wording, an account-only payee)."""
    keys = row.keys() if hasattr(row, "keys") else ()
    texts = [row["raw_text"] if "raw_text" in keys else None, row["raw_description"] if "raw_description" in keys else None]
    for t in texts:
        for rx in _BENEFICIARY_RES:
            m = rx.search(t or "")
            if m and _names(m.group(1)):
                return m.group(1).strip()
    # a YONO fund-transfer mail keeps its beneficiary as the description (its excerpt may stop before it)
    if ("source" in keys and row["source"] == "email" and re.search(r"fund transfer", texts[0] or "", re.I)
            and _names(texts[1]) and not _FALLBACK_DESC.match(texts[1] or "")):
        return texts[1].strip()
    return None


_FALLBACK_DESC = re.compile(r"^\s*[a-z]+ (?:account|card) transaction\s*$", re.I)


def _far_copy(conn, d):
    """d is a weakly linked SMS / email copy with no partner of the other alert source in its group
    within CROSS_ALERT_MAX_SECONDS (every such partner's time known and far)."""
    if not d["is_duplicate"] or d["source"] not in ("sms", "email") or d["merge_rule"] not in WEAK_RULES:
        return False
    partners = [m for m in _group(conn, d)
                if m["id"] != d["id"] and m["source"] in ("sms", "email") and m["source"] != d["source"]]
    if not partners or _confirmed(_text_cache(conn), [d], partners):
        return False                      # no partner, or one printing the same time / balance (proof)
    return all(CROSS_ALERT_MAX_SECONDS < _gap_s(conn, d, m) < float("inf") for m in partners)


def release_far_copies(conn, x, alias=None):
    """Guessed links yield to evidence. x found no match; release (make live again, unlinked) the copies
    that stand in its way only because of a guess, so x and they can pair with what is near:
      * a live alert near x cannot take x only because its group holds a far copy that provably is
        another transaction than x (T2's core-banking mail taken by T1's YONO SMS 30 min earlier
        blocks T1's own core-banking SMS, 28 s from that YONO SMS);
      * a copy near x, far from every partner in its own group, is compatible with x (T2's mail and
        T2's YONO SMS 24 s apart);
      * a same-alert twin (same amount, another template, minutes apart — a guess) whose own balance
        or time x's group prints, while its twin is provably another transaction than x's group (T1's
        core-banking mail paired 70 s away with T2's YONO mail before any SMS had arrived).
    Returns the released row ids (the caller re-links them)."""
    alias = alias if alias is not None else _card_alias_map(conn)
    if x["is_duplicate"]:
        return []
    text = _text_cache(conn)
    gx = _group(conn, x)
    mine = {r["id"] for r in gx}

    def conflicts(rows):
        return any(_two_transactions(conn, a, b, text) or transfer_payees_conflict(a, b) for a in gx for b in rows)

    release, unstamp = set(), {}
    for r in _alert_neighbours(conn, alias, x):
        if r["id"] in mine or _gap_s(conn, x, r) > CROSS_ALERT_MAX_SECONDS:
            continue
        group = _group(conn, r)
        confirmed = _confirmed(text, [r], gx)
        if r["is_duplicate"]:
            mates = [m for m in group if m["id"] != r["id"]]
            if conflicts([r]):
                continue
            # a same-alert guess is freed only when x's group can take it (no alert of its sender family
            # there yet): a copy freed with nowhere to go stays live (round 9, D1 / D2)
            if _far_copy(conn, r) or (r["merge_rule"] == "same-alert" and confirmed and conflicts(mates)
                                      and not any((m["source"], _family_of(conn, m)) ==
                                                  (r["source"], _family_of(conn, r)) for m in gx)):
                release.add(r["id"])
            continue
        blockers = [d for d in group if d["id"] != r["id"] and conflicts([d])
                    and (_far_copy(conn, d) or (d["merge_rule"] == "same-alert" and confirmed))]
        if not blockers:
            continue
        # a reference the winner only inherited from a blocker leaves with it
        stamped = {(b["ref_no"] or "").strip().upper() for b in blockers} - {""}
        rest = [_without_stamp(m, stamped) for m in group if m["id"] not in {b["id"] for b in blockers}]
        if not conflicts(rest):
            release.update(b["id"] for b in blockers)
            for m in rest:
                if m["ref_no"] is None and (_row(conn, m["id"])["ref_no"] or ""):
                    unstamp[m["id"]] = True
    if release:
        conn.executemany("UPDATE transactions SET is_duplicate = 0, matched_txn_id = NULL, merge_rule = NULL "
                         "WHERE id = ?", [(i,) for i in release])
    if unstamp:
        conn.executemany("UPDATE transactions SET ref_no = NULL WHERE id = ?", [(i,) for i in unstamp])
    return sorted(release)


def _without_stamp(row, stamped):
    """`row` as a dict, minus a stored reference that is one of `stamped` and that its own text does not
    print (it was inherited from a copy linked to it)."""
    d = dict(row)
    ref = (d.get("ref_no") or "").strip().upper()
    if ref and ref in stamped and d.get("source") == "sms":
        from modules.sms.parser import extract_ref_no
        if (extract_ref_no(d.get("raw_text") or "") or "").upper() != ref:
            d["ref_no"] = None
    return d


def find_match(conn, x, alias=None):
    """Return (row, rule) for the row `x` should be linked with, or (None, None)."""
    alias = alias if alias is not None else _card_alias_map(conn)
    xc, cands = _candidates(conn, alias, x, REF_WINDOW_DAYS)
    x_ref = (x["ref_no"] or "").strip()

    # ref: identical bank reference -> same transaction (any source, +/-3 days). A long reference
    # (UTR / RRN, 10+ characters) is decisive even when the two copies name the account differently:
    # one bank's UPI-ATM SMS prints one number for the account and its alert email another, for the
    # same withdrawal and RRN, and both stayed live (Rs 9,100 counted twice, iter-18 verifier).
    y = ref_match(conn, x, alias)
    if y is not None:
        return y, "ref"

    near = [(y, yc) for (y, yc) in cands if _days_apart(x["date"], y["date"]) <= DATE_WINDOW_DAYS]

    # same-body / repeat-alert: SMS copies of ONE purchase, judged by when the phone got them.
    if x["source"] == "sms":
        sibling, rule = _sms_sibling(conn, alias, x, xc, x_ref)
        if sibling is not None:
            return sibling, rule

    # twin-alert: one account transfer reported by two SMS sender families (SBI sends both a YONO
    # "debited ... for transfer to Anita Ac Xx7777 ... Ref 200000017" and a core-banking "Debited
    # INR 3,00,000.00 ... -Transferred to Mrs. ANITA RAO" for the same Rs 3L). Same account,
    # amount, day and direction; different sender families; references do not conflict; and the
    # counterparty text corroborates — two genuine same-day transfers to different people stay two.
    if x["source"] == "sms" and xc and float(x["amount"]) >= TWIN_MIN_RS:
        xs = _sender_family(conn, x)
        x_text_ref = _ref_of(x)
        for y, yc in sorted(near, key=lambda p: (_gap_s(conn, x, p[0]), p[0]["id"])):
            if y["source"] != "sms" or y["date"] != x["date"] or yc != xc:
                continue
            if abs(float(y["amount"]) - float(x["amount"])) >= 0.005:
                continue
            y_ref = _ref_of(y)
            if x_text_ref and y_ref and x_text_ref != y_ref:
                continue
            if _facts_conflict(x["raw_text"], y["raw_text"]):
                continue
            if _gap_s(conn, x, y) > TWIN_MAX_SECONDS:
                continue                      # a second transfer 30 min later is another transfer
            ys = _sender_family(conn, y)
            # two sender families of one bank, minutes apart: their texts name the payee differently
            # (YONO the loan account "Ac X9999", core banking the owner), so within
            # TWIN_UNCORROBORATED_SECONDS the texts need not agree; further apart they must
            close = _gap_s(conn, x, y) <= TWIN_UNCORROBORATED_SECONDS and not _payees_conflict(x, y)
            if (xs and ys and xs != ys and (close or merchants_corroborate(x, y))
                    and groups_compatible(conn, x, y)):
                return y, "twin-alert"

    def eligible(y, yc):
        if y["source"] == x["source"]:
            return False                      # genuine repeats stay separate
        y_ref = (y["ref_no"] or "").strip()
        if x_ref and y_ref and x_ref != y_ref:
            return False                      # two different references = two transactions
        if not (xc and yc):
            return False                      # card-less pairs only merge by reference
        if _claimed_by_source(conn, y["id"], x["source"]) or _claimed_by_source(conn, x["id"], y["source"]):
            return False
        # two transfers to different named people are two movements, whatever else matches
        # (iteration-198 re-verify: `unique` joined 'Asha' and 'Mr. RAVI KUMAR' alerts in 69 of 82 losses)
        if transfer_payees_conflict(x, y):
            return False
        # an SMS and an email far apart, while a nearer alert is provably another transaction
        if far_against_nearer(conn, alias, x, y):
            return False
        # the rows already behind x and y must not describe two different transactions (a second
        # transfer's core-banking email joined the first transfer's group, whose core-banking SMS
        # printed another balance)
        return groups_compatible(conn, x, y)

    exact = [y for (y, yc) in near if eligible(y, yc) and _amount_close(x, y) == "exact"]

    # unique: exactly one candidate each way
    if len(exact) == 1:
        y = exact[0]
        rev_alias_c, rev = _candidates(conn, alias, y, DATE_WINDOW_DAYS)
        rev_same_src = [
            z for (z, zc) in rev
            if z["source"] == x["source"] and _amount_close(y, z) == "exact"
            and not _claimed_by_source(conn, y["id"], z["source"])
            and not ((z["ref_no"] or "") and (y["ref_no"] or "") and z["ref_no"] != y["ref_no"])
        ]
        if len(rev_same_src) == 1 and rev_same_src[0]["id"] == x["id"] and not merchants_disagree(x, y):
            return y, "unique"

    # proof: several candidates and exactly one group prints x's own balance / time — before any guess
    # by merchant text or arrival order (round 9: a statement's two same-amount lines, line 1 holding the
    # mail that prints this SMS's time, and `count` paired the SMS with line 2)
    if len(exact) >= 2:
        text, gx = _text_cache(conn), _group(conn, x)
        proved = [y for y in exact if _confirmed(text, gx, _group(conn, y))]
        if len(proved) == 1:
            return proved[0], "proof"

    # merchant: several candidates — the corroborating one, closest date first
    corroborating = [y for y in exact if merchants_corroborate(x, y)]
    if corroborating:
        corroborating.sort(key=lambda y: (_days_apart(x["date"], y["date"]), _gap_s(conn, x, y), y["id"]))
        return corroborating[0], "merchant"

    # count: k identical purchases seen k times by each of two sources
    if len(exact) >= 2:
        y = _count_pair(conn, alias, x, xc, exact)
        if y is not None:
            return y, "count"

    # tolerance: near-equal amount, same card, merchant must corroborate
    tol = [y for (y, yc) in near if eligible(y, yc) and _amount_close(x, y) == "tolerance"
           and merchants_corroborate(x, y)]
    if tol:
        tol.sort(key=lambda y: (abs(float(y["amount"]) - float(x["amount"])), _days_apart(x["date"], y["date"])))
        return tol[0], "tolerance"
    return None, None


def apply_link(conn, a, b, rule):
    """Link rows a and b. The higher-priority source wins; returns (winner_id, loser_id)."""
    if a["id"] == b["id"]:
        raise ValueError(f"refusing to link transaction {a['id']} to itself")
    pa, pb = SOURCE_PRIORITY.get(a["source"], 0), SOURCE_PRIORITY.get(b["source"], 0)
    if pa > pb or (pa == pb and a["id"] < b["id"]):
        winner, loser = a, b
    else:
        winner, loser = b, a

    conn.execute(
        "UPDATE transactions SET is_duplicate = 1, matched_txn_id = ?, merge_rule = ? WHERE id = ?",
        (winner["id"], rule, loser["id"]),
    )
    # anything that pointed at the loser now points at the winner
    conn.execute(
        "UPDATE transactions SET matched_txn_id = ? WHERE matched_txn_id = ? AND id <> ?",
        (winner["id"], loser["id"], winner["id"]),
    )

    updates, params = [], []
    # An order email explains the purchase, not one copy of it: its link and the labels it gave move
    # to the copy that stays live (a statement line winning a month later stranded them on the SMS
    # copy, so the visible row lost the restaurant / order detail, iter-18 verifier).
    moved_order = False
    if (conn.execute("SELECT 1 FROM email_enrichments WHERE transaction_id = ? LIMIT 1", (loser["id"],)).fetchone()
            and not conn.execute("SELECT 1 FROM email_enrichments WHERE transaction_id = ? LIMIT 1",
                                 (winner["id"],)).fetchone()):
        conn.execute("UPDATE email_enrichments SET transaction_id = ? WHERE transaction_id = ?",
                     (winner["id"], loser["id"]))
        hand = winner["confidence"] == "manual" or conn.execute(
            "SELECT 1 FROM category_overrides WHERE transaction_id = ? LIMIT 1", (winner["id"],)).fetchone()
        if loser["confidence"] == "enriched" and not hand:
            updates += ["merchant = ?", "category = ?", "confidence = 'enriched'"]
            params += [loser["merchant"], loser["category"]]
            moved_order = True
    w_cat = winner["category"] or "Uncategorized"
    l_cat = loser["category"] or "Uncategorized"
    better_label = not moved_order and (
        (w_cat in ("Uncategorized", "Pass-Through") and l_cat not in ("Uncategorized", "Pass-Through"))
        or (winner["confidence"] in ("low", "medium") and loser["confidence"] == "high")
    )
    # A statement line is a card spend: never stamp a transfer-type guess onto it.
    if winner["source"] == "card" and l_cat in TRANSFER_LIKE:
        better_label = False
    if better_label and loser["merchant"]:
        updates += ["merchant = ?", "category = ?", "confidence = 'high'"]
        params += [loser["merchant"], l_cat]
    # a winner whose own text prints a reference keeps it; never stamp another row's ref onto it. Nor
    # across a far guess: T1's YONO reference stamped on T2's core-banking SMS (a `unique` pairing 30 min
    # apart) made T1's next alert a 'proof' match for T2 (iteration-198 re-verify)
    far_guess = rule in WEAK_RULES and CROSS_ALERT_MAX_SECONDS < _gap_s(conn, winner, loser) < float("inf")
    if not winner["ref_no"] and loser["ref_no"] and not _ref_of(winner) and not far_guess:
        updates.append("ref_no = ?"); params.append(loser["ref_no"])
    if not winner["occurred_at"] and loser["occurred_at"]:
        updates.append("occurred_at = ?"); params.append(loser["occurred_at"])
    if (loser["currency"] or "INR") != "INR" and (winner["currency"] or "INR") == "INR":
        # the statement has the exact INR amount; keep the original foreign amount for display
        updates += ["currency = ?", "original_amount = ?", "fx_estimated = 0"]
        params += [loser["currency"], loser["original_amount"]]
    if updates:
        conn.execute(f"UPDATE transactions SET {', '.join(updates)} WHERE id = ?", (*params, winner["id"]))
    if rule == "proof":
        _evict_far_copies(conn, winner["id"], a)
    return winner["id"], loser["id"]


def _evict_far_copies(conn, head_id, proven):
    """`proven` joined the group on proof. An alert of its own source that sits in the group only as a
    far guess (weak rule, every partner of the other source far away) and is itself far from `proven`
    is another movement's alert: a transfer's two mails come seconds apart, not hours (round 9: T1's
    YONO mail guessed onto T2's SMS 2h13m away, then T2's own core-banking mail proven there)."""
    head = _row(conn, head_id)
    for d in _group(conn, head):
        if (d["id"] != proven["id"] and d["source"] == proven["source"] and _far_copy(conn, d)
                and CROSS_ALERT_MAX_SECONDS < _gap_s(conn, d, proven) < float("inf")):
            conn.execute("UPDATE transactions SET is_duplicate = 0, matched_txn_id = NULL, merge_rule = NULL "
                         "WHERE id = ?", (d["id"],))


def link_on_insert(conn, txn_id, alias=None):
    """Called right after a row is inserted, so a purchase is never double-counted while it
    waits for the hourly sweep. Returns {'rule', 'winner', 'loser'} or None."""
    x = _row(conn, txn_id)
    if x is None or x["is_duplicate"]:
        return None
    y, rule, released = _match_releasing(conn, x, alias)
    out = None
    if y:
        w, l = apply_link(conn, x, y, rule)
        out = {"rule": rule, "winner": w, "loser": l}
    for rid in released:                       # each released copy pairs again with what is near it
        r = _row(conn, rid)
        if r is not None and not r["is_duplicate"]:
            ry, rrule = match_with_proof(conn, r, alias)
            if ry is not None:
                apply_link(conn, r, ry, rrule)
    return out


def backfill_sms_refs(conn=None, dry_run=True):
    """Store the reference every legacy SMS row prints but never had extracted (ref_no NULL).

    The `ref` rule and every "two different references" veto read ref_no; a NULL there let a
    second Rs 280 UPI payment (Ref 600000000016) merge into the first (Ref 600000000015), which
    then inherited the loser's reference (iter-18 verifier). Run before the merge sweep."""
    from modules.sms.parser import extract_ref_no
    conn = conn or get_db()
    rows = conn.execute(
        "SELECT id, raw_text FROM transactions WHERE source = 'sms' AND (ref_no IS NULL OR ref_no = '') "
        "AND raw_text IS NOT NULL AND raw_text <> ''"
    ).fetchall()
    found = [(extract_ref_no(r["raw_text"]), r["id"]) for r in rows]
    found = [(ref, i) for ref, i in found if ref]
    if not dry_run and found:
        conn.executemany("UPDATE transactions SET ref_no = ? WHERE id = ?", found)
        conn.commit()
    return {"checked": len(rows), "filled": len(found)}


def link_orphan_copies(conn=None, dry_run=True):
    """A legacy SMS row whose SMS record no longer exists cannot be compared by text or time (its
    stored text was rewritten: "... to Self Transfer"), so when a re-delivery of that alert was
    imported again it stayed live beside it (HSBC Rs 1,000 on 21 Mar 2026). Link such an orphan to
    the ONE live SMS row with the same card, day, amount, direction and category whose reference does
    not conflict; any other shape is left alone and reported."""
    conn = conn or get_db()
    orphans = conn.execute(
        f"SELECT {_ROW_COLS} FROM transactions t WHERE source = 'sms' AND is_duplicate = 0 "
        "AND NOT EXISTS (SELECT 1 FROM sms s WHERE s.id = t.source_ref)").fetchall()
    linked, left = [], []
    for o in orphans:
        twins = [y for y in conn.execute(
            f"SELECT {_ROW_COLS} FROM transactions t WHERE source = 'sms' AND is_duplicate = 0 AND id <> ? "
            "AND date = ? AND ABS(amount - ?) < 0.005 AND COALESCE(card_last4, '') = COALESCE(?, '') "
            "AND txn_type = ? AND COALESCE(category, '') = COALESCE(?, '') "
            "AND EXISTS (SELECT 1 FROM sms s WHERE s.id = t.source_ref)",
            (o["id"], o["date"], o["amount"], o["card_last4"], o["txn_type"], o["category"])).fetchall()
            if not (_ref_of(o) and _ref_of(y) and _ref_of(o) != _ref_of(y))]
        if len(twins) == 1:
            linked.append((o["id"], twins[0]["id"]))
            if not dry_run:
                apply_link(conn, o, twins[0], "orphan-copy")
        else:
            left.append(o["id"])
    if not dry_run:
        conn.commit()
    return {"dry_run": dry_run, "orphans": len(orphans), "linked": linked, "left": left}


def unique_links_refused(conn=None, unlink=False):
    """Iteration 203 E11 history: links the old `unique` rule made between two KNOWN, different brands
    with nothing tying them together. Listed always; with unlink=True the copy becomes a live row
    again (its winner keeps everything else). Links to an unknown / legal name are left alone."""
    conn = conn or get_db()
    refused = []
    for d in conn.execute(f"SELECT {_ROW_COLS} FROM transactions WHERE is_duplicate = 1 AND merge_rule = 'unique' "
                          "AND matched_txn_id IS NOT NULL ORDER BY date, id").fetchall():
        w = _row(conn, d["matched_txn_id"])
        if w is None or not merchants_disagree(d, w):
            continue
        refused.append({"loser": d["id"], "winner": w["id"], "date": d["date"], "amount": d["amount"],
                        "card_last4": d["card_last4"], "loser_text": (d["raw_description"] or "")[:40],
                        "winner_text": (w["raw_description"] or "")[:40]})
    if unlink and refused:
        conn.executemany("UPDATE transactions SET is_duplicate = 0, matched_txn_id = NULL, merge_rule = NULL "
                         "WHERE id = ?", [(r["loser"],) for r in refused])
        conn.commit()
    return {"unlinked": bool(unlink), "refused": refused}


def run_merge_sweep(conn=None, since=None):
    """Idempotent pass over non-duplicate rows (hourly, and once over history at deploy).

    Lower-priority sources are visited first so each email/SMS looks for its better copy."""
    conn = conn or get_db()
    alias = _card_alias_map(conn)
    q = f"SELECT {_ROW_COLS} FROM transactions WHERE is_duplicate = 0"
    params = []
    if since:
        q += " AND date >= ?"
        params.append(since)
    q += " ORDER BY CASE source WHEN 'email' THEN 0 WHEN 'sms' THEN 1 ELSE 2 END, date, id"
    rows = conn.execute(q, params).fetchall()
    stats = {"checked": 0, "linked": 0, "by_rule": {}, "released": 0}
    queue = [r["id"] for r in rows]
    while queue:
        x = _row(conn, queue.pop(0))     # re-read: an earlier link may have changed it
        if x is None or x["is_duplicate"]:
            continue
        stats["checked"] += 1
        y, rule, released = _match_releasing(conn, x, alias)
        if released:
            stats["released"] += len(released)
            queue.extend(released)       # the released copies look for their near partners too
        if y:
            apply_link(conn, x, y, rule)
            stats["linked"] += 1
            stats["by_rule"][rule] = stats["by_rule"].get(rule, 0) + 1
    conn.commit()
    return stats
