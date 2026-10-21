"""
The one way a transaction row gets written — iter-18 (HP3 / HP4).

Before iter-18 each source (realtime SMS, batch SMS, Gmail SMS backup, statement PDF) carried its
own INSERT with its own idea of dedup, and only an hourly cross-matcher reconciled them — so a
purchase could be double-counted for up to an hour, and longer whenever the matcher's category
gate refused the pair. Every path now calls insert_transaction(), which:

  1. is idempotent on (source, source_ref) — re-delivering the same SMS / email / PDF line is a no-op;
  2. categorises with the source as context (the person-name heuristic never touches card spends);
  3. records what the money movement intrinsically is (`kind`, see derive_kind);
  4. links the new row to an existing copy of the same purchase immediately (modules.merge).
"""
import re

from modules.categorizer import categorize
from modules import merge

# Contexts that mean "this is a credit-card spend/credit", not a bank-account movement.
CARD_CONTEXTS = {"card", "cc", "meal_card", "email-card"}

KINDS = {
    "purchase", "refund", "reversal", "cashback", "fee", "interest", "emi_principal",
    "emi_conversion", "atm", "transfer_in", "debit", "bill_payment",
}

_EMI_PRINCIPAL = re.compile(r"principal|emi\s*(?:amt|amount)|offus\s*emi", re.I)
# HDFC prints an instalment's interest as "OFFUS EMI,INT NBR:07,<loan>": "offus emi" made it principal
# and dropped it from spend (iter-203 E6; principal is "OFFUS EMI,PRIN NB:...").
_EMI_INTEREST_LINE = re.compile(r"offus\s*emi\s*,\s*int\b", re.I)
_EMI_CONVERSION = re.compile(r"emi\s*conver|convert(?:ed)?\s*(?:in)?to\s*emi|emi\s*booking|smart\s*emi|flexipay|flexi\s*emi", re.I)
_FEE = re.compile(r"\b(?:fee|fees|gst|igst|cgst|sgst|cess|surcharge|late\s*payment|finance\s*charge|overlimit|over\s*limit|dishonou?r)\b", re.I)
_INTEREST = re.compile(r"\binterest\b", re.I)


def derive_kind(txn_type, category, description, context=None, pattern_kind=None):
    """What happened, independent of where the money went.

    `pattern_kind` (from the parser pattern, e.g. 'refund' / 'reversal' / 'cashback' / 'atm' /
    'emi_principal') wins when given; otherwise the description, category and context decide."""
    if pattern_kind in KINDS:
        return pattern_kind
    d = (description or "").lower()
    cat = category or ""
    if txn_type == "credit":
        if _EMI_CONVERSION.search(d):
            return "emi_conversion"
        if "cashback" in d or "cash back" in d:
            return "cashback"
        if "revers" in d:
            return "reversal"
        if "refund" in d:
            return "refund"
        if cat == "Finance" or re.search(r"payment\s*(?:received|thank)|thank\s*you\s*for\s*(?:the\s*)?payment", d):
            return "bill_payment"
        if context in CARD_CONTEXTS:
            return "refund"           # any other credit on a credit card gives money back
        return "transfer_in"
    # debits
    if _EMI_CONVERSION.search(d) and context in CARD_CONTEXTS:
        return "emi_principal"
    if _EMI_INTEREST_LINE.search(d):
        return "interest"
    if _EMI_PRINCIPAL.search(d) and ("emi" in d or "amorti" in d or "principal" in d):
        return "emi_principal"
    if _INTEREST.search(d):
        return "interest"
    if cat == "Bank Charges" or _FEE.search(d):
        return "fee"
    if cat == "ATM Withdrawal" or context == "atm":
        return "atm"
    if cat == "Finance":
        return "bill_payment"
    if context in CARD_CONTEXTS:
        return "purchase"
    return "debit"


def find_existing(conn, source, source_ref):
    if not source_ref:
        return None
    r = conn.execute(
        "SELECT id FROM transactions WHERE source = ? AND source_ref = ? LIMIT 1",
        (source, str(source_ref)),
    ).fetchone()
    return r["id"] if r else None


def insert_transaction(conn, *, amount, date, source, raw_description, source_ref=None,
                       bank=None, card_last4=None, txn_type="debit", raw_text=None,
                       currency="INR", original_amount=None, fx_estimated=0, kind=None,
                       pattern_kind=None, ref_no=None, occurred_at=None, context=None,
                       merchant=None, category=None, confidence=None, link=True):
    """Insert one observed transaction and link it to any existing copy.

    Returns {'status': 'exists'|'inserted', 'id', 'merchant', 'category', 'confidence', 'kind',
    'linked'}. Raises ValueError for rows that cannot be valid (no amount / no date)."""
    if amount is None or float(amount) <= 0:
        raise ValueError("transaction amount must be positive")
    if not date:
        raise ValueError("transaction date is required")
    existing = find_existing(conn, source, source_ref)
    if existing:
        return {"status": "exists", "id": existing}

    if merchant is None or category is None:
        m, c, conf = categorize(raw_description, context=context, raw_text=raw_text)
        merchant = merchant if merchant is not None else m
        category = category if category is not None else c
        confidence = confidence if confidence is not None else conf
    confidence = confidence or "low"
    kind = kind or derive_kind(txn_type, category, raw_description, context, pattern_kind)

    cur = conn.execute(
        "INSERT INTO transactions (amount, raw_description, merchant, category, date, source, "
        "source_ref, bank, card_last4, txn_type, confidence, raw_text, currency, original_amount, "
        "fx_estimated, kind, ref_no, occurred_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (round(float(amount), 2), raw_description, merchant, category, date, source,
         str(source_ref) if source_ref is not None else None, bank, card_last4 or "",
         txn_type, confidence, raw_text, currency or "INR", original_amount,
         1 if fx_estimated else 0, kind, ref_no, occurred_at),
    )
    txn_id = cur.lastrowid
    linked = merge.link_on_insert(conn, txn_id) if link else None
    conn.commit()
    return {"status": "inserted", "id": txn_id, "merchant": merchant, "category": category,
            "confidence": confidence, "kind": kind, "linked": linked}
