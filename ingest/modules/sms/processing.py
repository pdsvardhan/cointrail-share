"""
One SMS -> one decision — iter-18 HP1.

Before iter-18 three code paths (realtime POST /sms, the batch process-pending, and the Gmail SMS-backup
import) each ran their own filter -> parse -> INSERT, with their own dedup, and a financial SMS that no
pattern understood was marked processed and forgotten. That is how Axis's 2026-10-16 rewording lost 6
of 7 Axis spends with nobody noticing.

process_sms_row() is now the only way an SMS becomes (or deliberately does not become) a transaction.
Every financial SMS ends in exactly one class:

  transaction    parsed; written through txn_store.insert_transaction (idempotent on the SMS id,
                 categorised with the account context, kind/currency/ref/time recorded, linked to any
                 copy of the same purchase immediately)
  bill_payment   a credit-card bill payment — services.cards_service owns those (cc_payments)
  statement      a statement / amount-due / overdue alert — cards_service reads the bank's numbers
  noise          known non-movement wordings (OTP, declined, pre-debit notice, merchant refund notice…)
  non_financial  no amount + money verb at all (password expiry, beneficiary added…)
  quarantined    carries money but nothing above recognised it -> parse_quarantine, sms.is_processed = 2

sms.is_processed: 0 pending, 1 done, 2 quarantined.
"""
import re
from collections import Counter

from modules import quarantine
from modules.sms.parser import parse_single_sms
from modules.sms.receiver import classify_financial, noise_reason, is_merchant_refund_notice
from modules.txn_store import insert_transaction
from services.cards_service import parse_payment_sms
from util.timez import ist_date_of, today_ist

# Statement / amount-due / overdue alerts. The trigger words are those of
# cards_service.statements_from_sms_logic (which reads the bank's printed totals and due dates from
# these), plus the dues reminders the 2026-10-20 corpus showed it never selected. Each line cites a
# real body. Recognised here so the message is neither noise nor quarantined; the cards service owns
# what it means.
_STATEMENT_PATTERNS = [
    r"Statement.*Total due",                                   # HDFC "Credit Card XX4956 Statement:\nTotal due:" (4247, 1676)
    r"statement generated.*Total",
    r"Statement Generated:\s*For HDFC Bank Credit Card",       # HDFC (5513)
    r"credit card bill for .*(?:has been generated|Total amount)",   # CRED (2983, 2969)
    r"Total amount due",                                       # Axis e-statement (914)
    r"\btotal due of\b",                                       # ICICI "Kindly pay total due of Rs" (1248)
    r"Pay Total (?:Amount )?Due of Rs",                        # ICICI (3065, 5494)
    r"Total of Rs\.?\s*[\d,.]+ (?:or minimum of Rs\.?\s*[\d,.]+ )?is (?:due|overdue)",   # ICICI (5463, 4093)
    r"Rs\.?\s*[\d,.]+ is due on ICICI Bank Credit Card",       # ICICI (4500)
    r"total payment due on HSBC",                              # HSBC (347)
    r"HSBC Credit Card ending \d{4}\s*:\s*Total amount is",    # HSBC (2813, 1566)
    r"statement for your credit card ending \d{4} is ready",   # HSBC (271)
    r"statement for (?:your )?Axis Bank Credit Card",          # Axis (5619, 2342, 1215)
    r"E-Statement of (?:your )?Axis Bank Credit Card",         # Axis (914, 712)
    r"Payment of INR [\d,.]+ for Axis Bank Credit Card no\.? XX\d{4} is due on",   # Axis due reminder (4777)
    r"bill of INR [\d,.]+ is overdue",                         # Axis (3088)
    r"E-statement of SBI Credit Card",                         # SBI (5404)
    r"outstanding of Rs\.?\s*[\d,.]+,? on your credit card ending \d{2,4} is due on",   # SBI (3136)
    r"Amount Due\s*Rs\.?\s*[\d,.]+ on HDFC Bank Credit Card",  # HDFC "Amount Due\nRs.15336 ... Pay instantly by" (4244)
    r"Amt Due:?\s*Rs\.?\s*[\d,.]+ on HDFC Bank",               # HDFC (2480, 355)
    r"link to quickly pay .* Credit Card dues",                # HDFC (3375)
    r"non-receipt of payment for .* statement",                # HDFC overdue (3374)
]
_STATEMENT_RES = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _STATEMENT_PATTERNS]

PROCESSED = 1
QUARANTINED = 2


def is_statement_alert(body):
    return any(rx.search(body or "") for rx in _STATEMENT_RES)


def _row(sms_row):
    """Accept sqlite3.Row or dict."""
    return sms_row if isinstance(sms_row, dict) else dict(sms_row)


def received_ist_date(sms_row):
    """The IST calendar date the phone received the SMS (phone_timestamp; else the server's
    received_at; else today). Both are stored as naive UTC — the IST conversion is what keeps an
    alert received at 00:30 IST on the 1st from landing on the 31st (iter-18 M8)."""
    for key in ("phone_timestamp", "received_at"):
        d = ist_date_of(sms_row.get(key))
        if d:
            return d
    return today_ist().isoformat()


def _cashback_card(conn, lookup):
    """Resolve the card a cardless cashback SMS is for, from the owner's card config — never a
    hard-coded last4 (a reissued card changes it). Unique active match only."""
    if lookup != "sbi_cashback":
        return ""
    rows = conn.execute(
        "SELECT last4, active FROM cards WHERE bank = 'SBI' AND card_name LIKE '%Cashback%'"
    ).fetchall()
    active = [r["last4"] for r in rows if r["active"]]
    if len(active) == 1:
        return active[0]
    if not active and len(rows) == 1:
        return rows[0]["last4"]
    return ""


def classify_sms_row(conn, sms_row):
    """Decide what an SMS is, without writing anything (reprocess_history's dry run relies on that).

    Returns {'class', 'reason'?, 'txn'?: insert_transaction kwargs, 'payment'?}."""
    s = _row(sms_row)
    body = s.get("body") or ""
    if not body.strip():
        return {"class": "non_financial", "reason": "empty"}

    # Bill payments and statement alerts first: their wordings overlap noise rules ("PAYMENT OF Rs ...
    # RECEIVED TOWARDS YOUR CREDIT CARD", "Amt Due ... Pay Instant") and they must reach the cards
    # service as what they are.
    payment = parse_payment_sms(body)
    if payment:
        return {"class": "bill_payment", "reason": "card_payment", "payment": payment}
    if is_statement_alert(body):
        return {"class": "statement", "reason": "statement_alert"}

    reason = noise_reason(body)
    if reason:
        return {"class": "noise", "reason": reason}

    ref_date = received_ist_date(s)
    parsed = parse_single_sms(body, reference_date=ref_date)
    if parsed:
        if parsed["amount"] is None:
            # a foreign currency util.fx has no rate for: never store it as if it were rupees
            return {"class": "quarantined", "reason": f"no_fx_rate:{parsed['currency']}", "pattern": parsed["pattern"]}
        card = parsed["card_last4"]
        if parsed.get("card_lookup"):
            card = _cashback_card(conn, parsed["card_lookup"])
        txn = {
            "amount": parsed["amount"],
            # A message that prints no date is dated by when the phone received it, in IST.
            "date": parsed["date"] or ref_date,
            "source": "sms",
            "source_ref": s.get("id"),
            "raw_description": parsed["description"],
            "bank": parsed["bank"],
            "card_last4": card,
            "txn_type": parsed["txn_type"],
            "raw_text": body,
            "currency": parsed["currency"],
            "original_amount": parsed["original_amount"],
            "fx_estimated": parsed["fx_estimated"],
            "pattern_kind": parsed["kind"],
            "ref_no": parsed["ref_no"],
            "occurred_at": parsed["occurred_at"],
            "context": parsed["source"],
        }
        return {"class": "transaction", "reason": parsed["pattern"], "pattern": parsed["pattern"], "txn": txn}

    if quarantine.looks_like_money_movement(body):
        return {"class": "quarantined", "reason": "no_pattern"}
    return {"class": "non_financial", "reason": "no_money"}


def _set_processed(conn, sms_id, state):
    if sms_id is not None:
        conn.execute("UPDATE sms SET is_processed = ? WHERE id = ?", (state, sms_id))


def process_sms_row(conn, sms_row):
    """Classify one SMS and act on it. Idempotent: re-running on the same row never adds a second
    transaction (insert_transaction is keyed on source='sms', source_ref=sms.id).

    Returns the classify_sms_row dict plus, for transactions, 'status' ('inserted'|'exists'),
    'transaction_id' and the categorised 'merchant'/'category'/'confidence'/'kind'."""
    s = _row(sms_row)
    sms_id = s.get("id")
    out = classify_sms_row(conn, s)

    if out["class"] == "transaction":
        try:
            res = insert_transaction(conn, **out["txn"])
        except ValueError as e:
            out = {"class": "quarantined", "reason": f"invalid:{e}", "pattern": out.get("pattern")}
        else:
            out["status"] = res["status"]
            out["transaction_id"] = res["id"]
            for k in ("merchant", "category", "confidence", "kind", "linked"):
                if k in res:
                    out[k] = res[k]

    if out["class"] == "quarantined":
        if sms_id is not None:
            quarantine.quarantine(conn, "sms", sms_id, s.get("sender"), s.get("body"), out["reason"])
        _set_processed(conn, sms_id, QUARANTINED)
    else:
        if sms_id is not None:
            # A held message that now classifies (a pattern was added) leaves the quarantine.
            quarantine.resolve(conn, "sms", sms_id, out["class"], out.get("transaction_id"))
        _set_processed(conn, sms_id, PROCESSED)
    conn.commit()
    return out


def process_pending(conn, limit=500):
    """Run every pending financial SMS through process_sms_row. Used by POST /sms/process-pending,
    the hourly sms_process task and the Gmail SMS-backup import.

    No amount+date+description dedup any more (iter-18 M4): it merged genuine repeat payments
    (5 x Rs 280 to one payee -> 1 row). Idempotency is per SMS id; two SMS rows describing one
    purchase are linked by modules.merge (same body / bank reference)."""
    pending = conn.execute(
        "SELECT id, sender, body, phone_timestamp, received_at FROM sms "
        "WHERE is_financial = 1 AND is_processed = 0 ORDER BY received_at ASC, id ASC LIMIT ?",
        (limit,),
    ).fetchall()
    by_class = Counter()
    inserted = 0
    failures = []
    for row in pending:
        try:
            out = process_sms_row(conn, row)
        except Exception as e:
            # A crash on one message must not stall the queue or vanish: hold it for review.
            quarantine.quarantine(conn, "sms", row["id"], row["sender"], row["body"], f"error:{e}"[:200])
            _set_processed(conn, row["id"], QUARANTINED)
            conn.commit()
            failures.append({"sms_id": row["id"], "error": str(e)})
            by_class["quarantined"] += 1
            continue
        by_class[out["class"]] += 1
        if out["class"] == "transaction" and out.get("status") == "inserted":
            inserted += 1
    return {"processed": inserted, "total_pending": len(pending), "by_class": dict(by_class),
            "failed": len(failures), "failures": failures[:20]}


def retry_quarantine(conn):
    """Re-run every open SMS quarantine entry (after a pattern or rule was added).

    Returns {retried, parsed (now transactions), resolved (by class), still_open}."""
    rows = conn.execute(
        "SELECT s.id, s.sender, s.body, s.phone_timestamp, s.received_at "
        "FROM parse_quarantine q JOIN sms s ON s.id = CAST(q.source_ref AS INTEGER) "
        "WHERE q.source = 'sms' AND q.resolved_at IS NULL ORDER BY s.id"
    ).fetchall()
    resolved = Counter()
    parsed = 0
    for row in rows:
        out = process_sms_row(conn, row)
        if out["class"] == "quarantined":
            continue
        resolved[out["class"]] += 1
        if out["class"] == "transaction":
            parsed += 1
    still_open = conn.execute(
        "SELECT COUNT(*) c FROM parse_quarantine WHERE source = 'sms' AND resolved_at IS NULL"
    ).fetchone()["c"]
    return {"retried": len(rows), "parsed": parsed, "resolved": dict(resolved), "still_open": still_open}


def reprocess_history(conn, since=None, dry_run=True):
    """Re-run classification over financial SMS that were marked done but produced no transaction —
    the silently dropped history (Axis new format, refunds, SBI e-mandates / savings UPI, HSBC account
    debits). `since` is an ISO date on the SMS receive time. With dry_run nothing is written.

    Also re-examines rows stored as NOT financial that receiver.classify_financial now accepts
    (`reclassified_financial`): Pluxee fee SMS ("Rs. 10.00 deducted from your Pluxee Card") carried one
    keyword and were never processed at all. A non-dry run sets is_financial = 1 on those first.

    Stats: counts by outcome and parser pattern, would-be transaction totals, and `possible_twins` —
    would-be transactions whose card + amount + direction already has a different-body SMS row within a
    day (two wordings of one event, which modules.merge cannot link without a shared reference)."""
    no_txn = ("NOT EXISTS (SELECT 1 FROM transactions t WHERE t.source = 'sms' "
              "AND t.source_ref = CAST(s.id AS TEXT))")
    since_sql, params = "", []
    if since:
        since_sql = " AND substr(COALESCE(s.phone_timestamp, s.received_at), 1, 10) >= ?"
        params.append(str(since)[:10])
    cols = "s.id, s.sender, s.body, s.phone_timestamp, s.received_at"
    rows = conn.execute(
        f"SELECT {cols} FROM sms s WHERE s.is_financial = 1 AND s.is_processed = 1 AND {no_txn}{since_sql} "
        "ORDER BY s.id", params).fetchall()
    newly_financial = [
        r for r in conn.execute(
            f"SELECT {cols} FROM sms s WHERE s.is_financial = 0 AND {no_txn}{since_sql} ORDER BY s.id",
            params).fetchall()
        if classify_financial(r["sender"] or "", r["body"] or "")
    ]

    by_class, by_pattern, quarantine_reasons = Counter(), Counter(), Counter()
    txn = {"count": 0, "debit_total": 0.0, "credit_total": 0.0, "by_kind": Counter()}
    possible_twins = []
    reclassify_ids = {r["id"] for r in newly_financial}
    for row in rows + newly_financial:
        if not dry_run and row["id"] in reclassify_ids:
            conn.execute("UPDATE sms SET is_financial = 1 WHERE id = ?", (row["id"],))
        out = classify_sms_row(conn, row) if dry_run else process_sms_row(conn, row)
        cls = out["class"]
        by_class[cls] += 1
        if cls == "quarantined":
            quarantine_reasons[out["reason"]] += 1
        if cls != "transaction":
            continue
        by_pattern[out.get("pattern")] += 1
        t = out.get("txn")
        if t is None:
            continue
        txn["count"] += 1
        txn["debit_total" if t["txn_type"] == "debit" else "credit_total"] += float(t["amount"])
        txn["by_kind"][t["pattern_kind"] or "(derived)"] += 1
        if t["card_last4"]:
            twin = conn.execute(
                "SELECT id FROM transactions WHERE source = 'sms' AND is_duplicate = 0 AND card_last4 = ? "
                "AND txn_type = ? AND ABS(amount - ?) < 0.005 AND date BETWEEN date(?, '-1 day') AND date(?, '+1 day') "
                "AND COALESCE(raw_text, '') <> ? LIMIT 1",
                (t["card_last4"], t["txn_type"], t["amount"], t["date"], t["date"], t["raw_text"]),
            ).fetchone()
            if twin:
                possible_twins.append({"sms_id": row["id"], "existing_txn": twin["id"]})
    txn["debit_total"] = round(txn["debit_total"], 2)
    txn["credit_total"] = round(txn["credit_total"], 2)
    txn["by_kind"] = dict(txn["by_kind"])
    return {
        "dry_run": bool(dry_run),
        "since": since,
        "examined": len(rows) + len(newly_financial),
        "reclassified_financial": len(newly_financial),
        "by_class": dict(by_class),
        "by_pattern": dict(by_pattern.most_common()),
        "quarantine_reasons": dict(quarantine_reasons),
        "transactions": txn,
        "possible_twins": len(possible_twins),
        "possible_twin_examples": possible_twins[:20],
    }


_GENERIC_DESC = re.compile(r"^(?:unknown|hdfc|sbi|hsbc|icici|axis|pluxee)\s+(?:debit|credit)$", re.I)
_FOREIGN_CCY = re.compile(r"\b(?:USD|EUR|GBP|AED|SGD)\s*\d", re.I)


def refresh_existing_sms_rows(conn, dry_run=True):
    """Re-read SMS rows the OLD parser stored badly, with the current parser (iter-18 staging finding).

    reprocess_history only looks at SMS that produced no row. These produced a WRONG row and so were
    never revisited: generic 'Unknown debit' rows (SBI e-mandate "Transaction Successful! ...
    Merchant: NETFLIX ... Date: 03-Oct-2026" became "Unknown debit" dated by the UTC arrival day),
    foreign charges stored as rupees (USD 118 as Rs 118), and 'Unknown'-bank rows.

    For each such row, from its own source SMS:
      - a specific parse now -> the row is corrected in place (same id): date, description,
        merchant/category (with account context), kind, reference, currency, time; then linked to any
        other copy of the same purchase;
      - noise / statement / bill payment / non-financial now -> the row is retired
        (is_duplicate = 1, merge_rule = 'reclassified-<class>'), never deleted.
    Rows with a manual category override are left alone. With dry_run nothing is written."""
    from modules.categorizer import categorize
    from modules.merge import link_on_insert
    from modules.txn_store import derive_kind

    rows = conn.execute(
        "SELECT t.id, t.amount, t.date, t.raw_description, t.bank, t.card_last4, t.currency, t.confidence, "
        "t.raw_text, s.id AS sms_id, s.sender, s.body, s.phone_timestamp, s.received_at "
        "FROM transactions t JOIN sms s ON s.id = CAST(t.source_ref AS INTEGER) "
        "WHERE t.source = 'sms' AND t.is_duplicate = 0 "
        "AND NOT EXISTS (SELECT 1 FROM category_overrides o WHERE o.transaction_id = t.id)"
    ).fetchall()
    stats = Counter()
    examples = []
    for r in rows:
        generic = bool(_GENERIC_DESC.match((r["raw_description"] or "").strip())) or (r["bank"] or "") == "Unknown"
        foreign = bool(_FOREIGN_CCY.search(r["body"] or "")) and (r["currency"] or "INR") == "INR"
        if not (generic or foreign) or r["confidence"] == "manual":
            continue
        stats["examined"] += 1
        out = classify_sms_row(conn, {"id": r["sms_id"], "sender": r["sender"], "body": r["body"],
                                      "phone_timestamp": r["phone_timestamp"], "received_at": r["received_at"]})
        cls = out["class"]
        if cls == "transaction":
            t = out["txn"]
            still_generic = out.get("pattern") in ("generic_debit", "generic_credit")
            if still_generic and not foreign:
                stats["unchanged"] += 1
                continue
            merchant, category, confidence = categorize(t["raw_description"], context=t["context"])
            kind = derive_kind(t["txn_type"], category, t["raw_description"], t["context"], t["pattern_kind"])
            if len(examples) < 15:
                examples.append({"id": r["id"], "from": [r["raw_description"], r["amount"], r["date"]],
                                 "to": [t["raw_description"], t["amount"], t["date"], t["currency"], category]})
            stats["corrected"] += 1
            if dry_run:
                continue
            conn.execute(
                "UPDATE transactions SET amount = ?, date = ?, raw_description = ?, merchant = ?, category = ?, "
                "confidence = ?, kind = ?, ref_no = COALESCE(?, ref_no), currency = ?, original_amount = ?, "
                "fx_estimated = ?, occurred_at = COALESCE(?, occurred_at), "
                "card_last4 = CASE WHEN COALESCE(card_last4, '') = '' THEN ? ELSE card_last4 END, "
                "bank = CASE WHEN COALESCE(bank, '') IN ('', 'Unknown') THEN ? ELSE bank END WHERE id = ?",
                (round(float(t["amount"]), 2), t["date"], t["raw_description"], merchant, category, confidence,
                 kind, t["ref_no"], t["currency"] or "INR", t["original_amount"], 1 if t["fx_estimated"] else 0,
                 t["occurred_at"], t["card_last4"] or "", t["bank"], r["id"]),
            )
            link_on_insert(conn, r["id"])
        elif cls in ("noise", "statement", "bill_payment", "non_financial"):
            stats[f"retired_{cls}"] += 1
            if len(examples) < 15:
                examples.append({"id": r["id"], "retired_as": cls, "desc": r["raw_description"], "amount": r["amount"]})
            if not dry_run:
                conn.execute("UPDATE transactions SET is_duplicate = 1, merge_rule = ? WHERE id = ?",
                             (f"reclassified-{cls}", r["id"]))
        else:
            stats[f"left_{cls}"] += 1
    if not dry_run:
        conn.commit()
    return {"dry_run": bool(dry_run), **dict(stats), "examples": examples}


def retire_merchant_refund_notice_rows(conn):
    """iter-18 H5: credits that were minted from MERCHANT refund notices ("Refund of Rs X has been
    initiated for Swiggy order …", by the retired `refund_generic` pattern) double-net a refund once
    the bank's own credit SMS arrives. Mark them is_duplicate = 1, merge_rule = 'merchant-notice'
    (kept for audit, never deleted). Returns the number of rows retired; idempotent."""
    rows = conn.execute(
        "SELECT id, raw_text FROM transactions WHERE source = 'sms' AND txn_type = 'credit' AND is_duplicate = 0"
    ).fetchall()
    ids = [r["id"] for r in rows if is_merchant_refund_notice(r["raw_text"])]
    for txn_id in ids:
        conn.execute(
            "UPDATE transactions SET is_duplicate = 1, merge_rule = 'merchant-notice' WHERE id = ?", (txn_id,)
        )
    conn.commit()
    return len(ids)


def decode_html_entities(conn=None, dry_run=True):
    """SMS Backup & Restore stores bodies HTML-escaped, so older rows carry "TNGO&#39;S C" in their
    description and merchant. The parser decodes new ones; this fixes the stored ones. Idempotent."""
    import html
    from db import get_db
    conn = conn or get_db()
    rows = conn.execute(
        "SELECT id, raw_description, merchant FROM transactions WHERE source = 'sms' AND "
        "(raw_description LIKE '%&#%' OR raw_description LIKE '%&amp;%' OR merchant LIKE '%&#%' OR merchant LIKE '%&amp;%')"
    ).fetchall()
    fixed = [(html.unescape(r["raw_description"] or "") or None, html.unescape(r["merchant"] or "") or None, r["id"])
             for r in rows]
    if not dry_run and fixed:
        conn.executemany("UPDATE transactions SET raw_description = ?, merchant = ? WHERE id = ?", fixed)
        conn.commit()
    return {"dry_run": dry_run, "rows": len(fixed)}
