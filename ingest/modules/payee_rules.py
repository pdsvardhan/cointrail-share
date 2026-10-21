"""Payee rules — iter-203 E3 / E9 / E10.

A payee rule is the owner saying "every payment to THIS payee belongs in THIS category". It is
created only on request (E3: "also apply to all payments to <payee>"), never learned silently from
one correction (E9). A rule is keyed on something that identifies one payee and nothing else:

  vpa      the UPI ID in the description ("worker77@okaxis")
  account  the counterparty account's visible digits in an account-to-account transfer
           ("IMPS to a/c XXXXXXX999" -> '999'); E10: an account alias is data in this table, not code.
           Never one of the owner's own accounts, and only read where the message names the
           counterparty (iter-203 E9, verifier round 2)
  name     the exact payee text, normalised ("SVC CATERINGS" -> 'svc caterings')

Generic text names no payee and is refused with a reason: re-filing a bare "DEBIT" row must never
become a rule for every row containing "debit".

Rules are applied by modules.categorizer.categorize() before any alias (they are the owner's own
decision), to stored rows when the rule is created, and are undone row by row when it is deleted.
Rows the owner re-filed by hand (confidence 'manual' / a category_overrides row) are never touched.
"""
import re

from modules.upi import extract_vpa

RULE_CONFIDENCE = "rule"
# Kinds derived from the category (see routes.transactions._CATEGORY_DERIVED_KINDS); the others
# (refund, interest, emi_principal ...) describe what happened and never follow a re-filing.
CATEGORY_DERIVED_KINDS = {"bill_payment", "fee", "atm", "purchase", "debit", "transfer_in"}

# Words that say how money moved, not to whom. A name made only of these (plus digits) is no payee.
GENERIC_WORDS = {
    "debit", "debited", "credit", "credited", "upi", "neft", "imps", "rtgs", "transfer", "transferred",
    "trf", "payment", "payments", "pay", "paid", "pos", "ach", "nach", "ecs", "atm", "cash", "bank", "card",
    "txn", "transaction", "unknown", "to", "from", "by", "via", "for", "at", "the", "of", "on", "a", "ac",
    "acct", "account", "mr", "mrs", "ms", "self", "sent", "received", "request", "per", "your", "as", "si",
    "standing", "instruction", "mandate", "autopay", "online", "ref", "no", "inb", "internet", "banking",
    "emi", "interest", "fee", "fees", "charge", "charges", "gst", "igst", "cgst", "sgst", "tax", "refund",
    "reversal", "cashback", "withdrawal", "deposit", "fund", "funds", "money", "amount", "balance", "avl",
    "sbi", "hdfc", "icici", "axis", "hsbc", "kotak", "yes", "idfc", "pnb", "canara", "federal", "yono",
    "movement", "other", "misc", "and",
}
# Merchant labels the parsers/categorizer give a movement that names no payee.
_PLACEHOLDERS = {"account debit", "bank transfer", "internet banking transfer", "upi transfer",
                 "unknown", "self transfer", "card bill payment"}

# The counterparty account an account-to-account transfer names ("IMPS to a/c XXXXXXX999",
# "transfer to <name> Ac x9999", "towards account 111-222***-706"). Same shape as
# modules.categorizer._COUNTERPARTY_ACCT_RE.
_ACCOUNT_RE = re.compile(r"(?:a/c|\bac\b|acct|account)(?:\s*no\.?)?\s*([Xx\*\d][Xx\*\d-]*)", re.I)
MIN_ACCOUNT_DIGITS = 3

# The full message prints the owner's own account too ("Your a/c no. XXXXXXXX1234 is debited", "From HDFC
# Bank A/C *1234"), so in raw text an account counts only where the wording names the counterparty:
_ACCT = r"(?:a/c|ac|acct|account)(?:\s*no\.?)?\s*([Xx\*\d][Xx\*\d-]*)"
_COUNTERPARTY_IN_TEXT = (
    # "to a/c XXXXXXX999", "credited to a/c no. ...", "transfer to <name> Ac x9999",
    # "NEFT via Online to SBOI A/c ***4321", "towards account 111-222***-706" — never "to your A/c"
    re.compile(r"\b(?:to|towards)\s+(?!your\b)(?:[A-Za-z][\w.&'\-]*\s+){0,5}?" + _ACCT, re.I),
    # SBI IMPS: "Your a/c no. ... is debited ... and a/c XXXXXXX999 credited"
    re.compile(r"\band\s+" + _ACCT + r"\s+credited\b", re.I),
    # a credit's sender: "credited ... as transfer from A/c 111-222***-706"
    re.compile(r"\btransfer\s+from\s+" + _ACCT, re.I),
)


def normalize_name(text):
    """Lower-case payee text without reference numbers, punctuation or doubled spaces."""
    t = (text or "").lower()
    t = re.sub(r"\(upi[^)]*\)", " ", t)          # "(UPI 123456789012)" a bank appends
    t = re.sub(r"\d{6,}", " ", t)                 # references, phone numbers
    t = re.sub(r"[^a-z0-9&@.\- ]+", " ", t)
    return " ".join(t.split()).strip(" .-")


def _tails(matches):
    out = []
    for m in matches:
        tail = re.search(r"(\d+)$", m.group(1))
        if tail and len(tail.group(1)) >= MIN_ACCOUNT_DIGITS:
            out.append(tail.group(1))
    return out


def _account_digits(text):
    """Visible digits of every account a DESCRIPTION names (masked tails like '999'). The parsers put
    only the counterparty in raw_description, so every account there is a counterparty."""
    return _tails(_ACCOUNT_RE.finditer(text or ""))


def _counterparty_digits(raw_text):
    """Visible digits of the counterparty accounts a full bank message names (see _COUNTERPARTY_IN_TEXT)."""
    out = []
    for pattern in _COUNTERPARTY_IN_TEXT:
        out.extend(_tails(pattern.finditer(raw_text or "")))
    return out


def own_tails(conn):
    """The last digits of every account and card the owner holds (the cards table, active or not)."""
    if conn is None:
        return frozenset()
    return frozenset(r["last4"] for r in conn.execute("SELECT last4 FROM cards").fetchall() if r["last4"])


def is_own(digits, own):
    """A visible tail is one of the owner's own accounts — compared the way
    categorizer.own_account_transfer does: banks mask to 3 or 4 trailing digits, so a 3-digit tail is
    compared on 3 digits and a longer one on 4."""
    if len(digits) >= 4:
        return any(len(o) >= 4 and o[-4:] == digits[-4:] for o in own)
    return any(len(o) >= 3 and o[-3:] == digits[-3:] for o in own)


def payee_key(description, conn=None):
    """(key_type, key, label, None) for the payee a description names, or (None, None, None, reason).
    With `conn`, a description naming one of the owner's own accounts names no payee (E9 round 2:
    an IMPS to one of the owner's own accounts offered a rule over hundreds of unrelated payments)."""
    own = own_tails(conn)
    for digits in _account_digits(description):
        if is_own(digits, own):
            return (None, None, None, f"a/c ...{digits} is one of your own accounts: a transfer between "
                                      f"your own accounts names no payee")
    vpa, _handle, _psp = extract_vpa(description or "")
    if vpa:
        return "vpa", vpa.lower(), vpa, None
    accounts = _account_digits(description)
    if accounts:
        return "account", accounts[0], f"a/c ...{accounts[0]}", None
    name = normalize_name(description)
    if not name or name in _PLACEHOLDERS:
        return None, None, None, f"'{(description or '').strip()}' names no specific payee"
    meaningful = [w for w in re.findall(r"[a-z]+", name) if w not in GENERIC_WORDS and len(w) >= 2]
    if not meaningful:
        return None, None, None, f"'{(description or '').strip()}' names no specific payee (only generic words)"
    return "name", name, (description or "").strip(), None


def _account_matches(rule_key, digits):
    """A masked tail matches when one is the other's suffix and they share at least 3 digits."""
    if len(rule_key) < MIN_ACCOUNT_DIGITS or len(digits) < MIN_ACCOUNT_DIGITS:
        return False
    return digits.endswith(rule_key) or rule_key.endswith(digits)


def rule_matches(rule, description, raw_text=None, own=frozenset()):
    """`own` = own_tails(conn): an account rule never matches through one of the owner's own accounts."""
    kt, key = rule["key_type"], rule["key"]
    if kt == "vpa":
        vpa = extract_vpa(description or "")[0] or extract_vpa(raw_text or "")[0]
        return bool(vpa) and vpa.lower() == key
    if kt == "account":
        digits = _account_digits(description) + _counterparty_digits(raw_text)
        return any(_account_matches(key, d) for d in digits if not is_own(d, own))
    if kt == "name":
        return normalize_name(description) == key
    return False


def load_rules(conn):
    try:
        return conn.execute(
            "SELECT id, key_type, key, label, category, merchant_name, source FROM payee_rules ORDER BY id"
        ).fetchall()
    except Exception:
        return []          # a database from before iter-203 has no table yet


def match(conn, description, raw_text=None, rules=None):
    """The rule that applies to this payee, or None. VPA rules are tried before account rules,
    account rules before name rules (the most specific identity wins)."""
    rules = load_rules(conn) if rules is None else rules
    own = own_tails(conn) if any(r["key_type"] == "account" for r in rules) else frozenset()
    order = {"vpa": 0, "account": 1, "name": 2}
    for rule in sorted(rules, key=lambda r: order.get(r["key_type"], 3)):
        if rule_matches(rule, description, raw_text, own):
            return rule
    return None


def as_dict(rule):
    return {k: rule[k] for k in ("id", "key_type", "key", "label", "category", "merchant_name", "source")} | (
        {"created_at": rule["created_at"]} if "created_at" in rule.keys() else {})


def _protected_ids(conn):
    return {r["transaction_id"] for r in conn.execute(
        "SELECT DISTINCT transaction_id FROM category_overrides WHERE transaction_id IS NOT NULL").fetchall()}


def apply_rule(conn, rule):
    """Label every stored row of this payee with the rule (both copies of a merged purchase), except
    rows the owner re-filed by hand and card bill payments. Returns the ids changed."""
    from modules.categorizer import card_numbers
    from modules.kinds_backfill import kind_for_row

    protected = _protected_ids(conn)
    cards = card_numbers(conn)
    own = own_tails(conn)
    changed = []
    rows = conn.execute(
        "SELECT id, source, card_last4, txn_type, raw_description, raw_text, merchant, category, confidence, "
        "kind FROM transactions"
    ).fetchall()
    for r in rows:
        if r["id"] in protected or r["confidence"] == "manual" or r["kind"] == "bill_payment":
            continue
        if not rule_matches(rule, r["raw_description"], r["raw_text"], own):
            continue
        merchant = rule["merchant_name"] or r["merchant"] or r["raw_description"]
        if (r["category"], r["merchant"], r["confidence"]) == (rule["category"], merchant, RULE_CONFIDENCE):
            continue
        kind = r["kind"]
        if kind in CATEGORY_DERIVED_KINDS:
            # a kind read off the category (bill_payment / fee / atm / ...) follows the new category,
            # exactly as for a hand re-filing (routes.transactions.update_transaction)
            probe = dict(r) | {"category": rule["category"]}
            kind = kind_for_row(probe, cards)
        conn.execute("UPDATE transactions SET category = ?, merchant = ?, confidence = ?, kind = ? WHERE id = ?",
                     (rule["category"], merchant, RULE_CONFIDENCE, kind, r["id"]))
        changed.append(r["id"])
    conn.commit()
    return changed


def save_rule(conn, key_type, key, label, category, merchant_name=None, source="user", txn_id=None):
    """Create or update the rule for one payee and apply it to history. Returns (rule, changed_ids)."""
    if key_type not in ("vpa", "account", "name") or not key:
        raise ValueError("a rule needs key_type vpa|account|name and a key")
    if not category or not str(category).strip():
        raise ValueError("a rule needs a category")
    conn.execute(
        "INSERT INTO payee_rules (key_type, key, label, category, merchant_name, source, created_from_txn_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(key_type, key) DO UPDATE SET label = excluded.label, category = excluded.category, "
        "merchant_name = excluded.merchant_name, source = excluded.source, updated_at = datetime('now')",
        (key_type, key, label, category.strip(), merchant_name, source, txn_id),
    )
    conn.commit()
    rule = conn.execute("SELECT * FROM payee_rules WHERE key_type = ? AND key = ?", (key_type, key)).fetchone()
    return rule, apply_rule(conn, rule)


def delete_rule(conn, rule_id):
    """Remove a rule and give every row it labelled back the label the categorizer gives it without
    the rule. Returns the ids relabelled, or None when the rule does not exist."""
    from modules.categorizer import card_numbers, categorize, context_for_row

    rule = conn.execute("SELECT * FROM payee_rules WHERE id = ?", (rule_id,)).fetchone()
    if not rule:
        return None
    conn.execute("DELETE FROM payee_rules WHERE id = ?", (rule_id,))
    conn.commit()
    cards = card_numbers(conn)
    own = own_tails(conn)
    relabelled = []
    for r in conn.execute(
        "SELECT id, source, card_last4, raw_description, raw_text FROM transactions WHERE confidence = ?",
        (RULE_CONFIDENCE,),
    ).fetchall():
        if not rule_matches(rule, r["raw_description"], r["raw_text"], own):
            continue
        ctx = context_for_row(r["source"], r["card_last4"], r["raw_text"] or r["raw_description"], cards)
        merchant, category, confidence = categorize(r["raw_description"], context=ctx, raw_text=r["raw_text"])
        conn.execute("UPDATE transactions SET merchant = ?, category = ?, confidence = ? WHERE id = ?",
                     (merchant, category, confidence, r["id"]))
        relabelled.append(r["id"])
    conn.commit()
    return relabelled


def matching_count(conn, key_type, key):
    probe = {"key_type": key_type, "key": key}
    own = own_tails(conn)
    n = 0
    for r in conn.execute("SELECT raw_description, raw_text FROM transactions WHERE is_duplicate = 0").fetchall():
        if rule_matches(probe, r["raw_description"], r["raw_text"], own):
            n += 1
    return n
