"""
Relations between rows that are NOT copies of one transaction — iter-18 (1442, honest totals).

modules.merge links copies of one money movement. The rows paired here are different movements
that belong together and change what each of them means:

  own-transfer  a bare debit on one of the owner's accounts and the same-amount bare credit on
                another of the owner's accounts that day or the next (one HSBC account "debited ... as per
                your request" + the other "credited ... as per the transaction"). The money never
                left the owner: Rs 15 lakh of these had been counted as "Sent to people". Both
                rows become Self Transfer.
  card-autopay  a bare account debit equal to a payment received on one of the owner's ACTIVE
                cards within 3 days (an HSBC account "as DEBIT" Rs 5,236.99 = the HSBC card's "CREDIT CARD
                PAYMENT"). A card bill, not money sent to anyone: the debit becomes Finance /
                bill_payment.
  returned      a bare debit and the same-amount bare credit on the SAME account the same day: the
                debit bounced and came straight back (an HSBC account "as DEBIT" Rs 45,000 leaving
                "avl Bal 0.00", then "as CREDIT" Rs 45,000 restoring the balance). The credit becomes
                that debit's reversal, so the two net to nothing.
  reversal      a reversal credit carrying the bank reference of the debit it undoes takes that
                debit's category, so it nets where the debit was counted (an ATM reversal filed as
                Transfer never reduced the ATM spend). One reversal per debit.
  loan          (iteration 203 E7) SBI words the monthly EMI as "Debited INR 75,000.00 -Transferred
                to Mr. <owner>": an internal transfer to the owner's own LOAN account, which the
                owner-name alias files as Self Transfer, so ~Rs 8.85 lakh of 2026 EMIs vanished from
                Loan repayments. Three pieces of evidence make such a debit a Loan Repayment:
                  - the loan account's own SMS for the same amount within 2 days ("Your loan A/C
                    XXXXX7777 Credited INR 75,000.00 ... deposit transfer from Mr. <owner>"); both
                    rows become Loan Repayment (the credit counts nowhere: it is the same money);
                  - the debit names a known loan account ("SI Fund Transfer ... to . Ac X7777");
                  - an own-name transfer (no account named) of an amount already proven to be an
                    EMI from the SAME account (a standing-instruction "to Loan A/c" SMS, a pair
                    above, or the owner's own Loan Repayment label): Rs 40,000 every month.

"Bare" means the bank printed no counterparty, only "DEBIT" / "per your request" / "per the
transaction": a debit that names a payee is a payment to that payee, whatever arrives elsewhere.
Pairs are 1:1 (an ambiguous match pairs nothing), never touch a row the owner labelled by hand,
and mark what they change with confidence 'paired', which recategorise leaves alone. Idempotent.
"""
import re
from collections import defaultdict
from datetime import date as _date

from db import get_db

PAIRED = "paired"
AUTOPAY_MAX_DAYS = 3
REVERSAL_MAX_DAYS = 7

# HSBC "as DEBIT" / "as per your request"; SBI core banking's alert e-mail names no payee either
# ("SBI ACCOUNT TRANSACTION": two auto-debits paying the SBI 2971 card bill stayed "Sent to people").
_BARE_DEBIT = re.compile(r"^\s*(?:(?:as\s+)?(?:debit|(?:as\s+)?per\s+your\s+request)|(?:[a-z]+\s+)?account\s+transaction)"
                         r"\s*\.?\s*$", re.I)
_BARE_CREDIT = re.compile(r"^\s*(?:as\s+)?(?:credit|(?:as\s+)?per\s+(?:the\s+transaction|your\s+request))\s*\.?\s*$", re.I)

LOAN_REPAYMENT = "Loan Repayment"
LOAN_MAX_DAYS = 2
# "Your loan A/C XXXXX7777 Credited INR 25,000.00 on 05/06/26,deposit transfer from Mr. ..." (SBI)
_LOAN_CREDIT = re.compile(r"\bloan\s*(?:a/?c|acct|account)\b[^.]{0,40}?\bcredited\b", re.I)
# the loan account's number in that SMS or in a standing instruction "... to Loan A/c No.XXXXX7777"
_LOAN_ACCOUNT = re.compile(r"\bloan\s*(?:a/?c|acct|account)\s*(?:no\.?)?\s*[x*#.]*\s*(\d{3,})", re.I)
# an account a debit names: "Ac X9901", "a/c XXXXXXX999", "A/c ***4321", "account 123-456***-789"
_NAMED_ACCOUNT = re.compile(r"\b(?:a/?c|acct|account)\b\.?\s*(?:no\.?)?\s*([x*#\d][x*#\d\-]*)", re.I)

_ROW = ("id, date, amount, txn_type, card_last4, category, kind, confidence, ref_no, raw_description, "
        "is_duplicate, matched_txn_id, raw_text")


def _days(a, b):
    """b - a in days."""
    return (_date.fromisoformat(b[:10]) - _date.fromisoformat(a[:10])).days


def _accounts(conn):
    """card_last4 as stored on a row -> the owner's account (cards row, active = 0) it names.
    Banks mask to 3 or 4 trailing digits, so 3-digit tails are compared on 3 digits."""
    own4, own3 = {}, {}
    for r in conn.execute("SELECT last4 FROM cards WHERE active = 0 AND last4 IS NOT NULL"):
        v = str(r["last4"])
        if len(v) >= 4:
            own4[v[-4:]] = v
        if len(v) >= 3:
            own3.setdefault(v[-3:], v)

    def account(card_last4):
        v = str(card_last4 or "").strip()
        if not v.isdigit():
            return None
        return own4.get(v[-4:]) if len(v) >= 4 else own3.get(v[-3:])
    return account


def _manual(conn):
    return {r[0] for r in conn.execute("SELECT transaction_id FROM category_overrides")}


def _same_tail(a, b):
    """Masked account numbers: compare 4 trailing digits when both have them, else 3."""
    a, b = str(a or ""), str(b or "")
    if len(a) >= 4 and len(b) >= 4:
        return a[-4:] == b[-4:]
    return len(a) >= 3 and len(b) >= 3 and a[-3:] == b[-3:]


def _named_account(text):
    """The trailing digits of the account a debit's text names, or None."""
    m = _NAMED_ACCOUNT.search(text or "")
    if not m:
        return None
    digits = re.findall(r"\d+", m.group(1))
    return digits[-1] if digits and len(digits[-1]) >= 3 else None


def _loan_repayments(live, account, usable, taken):
    """Loan EMIs paid by own-account transfer (see the module docstring, 'loan').

    Returns (pairs, named, by_amount): (debit, credit) pairs and lists of debit rows."""
    loan_credits = [r for r in live if r["txn_type"] == "credit" and usable(r)
                    and _LOAN_CREDIT.search(f"{r['raw_text'] or ''} {r['raw_description'] or ''}")]
    credit_ids = {r["id"] for r in loan_credits}
    loan_accounts = set()
    for r in live:
        is_credit = r["id"] in credit_ids
        if is_credit or (r["txn_type"] == "debit" and r["category"] == LOAN_REPAYMENT):
            m = _LOAN_ACCOUNT.search(f"{r['raw_text'] or ''} {r['raw_description'] or ''}")
            if m:
                loan_accounts.add(m.group(1))
        if is_credit and str(r["card_last4"] or "").isdigit():
            loan_accounts.add(str(r["card_last4"]))
    # a loan number that is also one of the owner's savings accounts would make every transfer there a loan
    loan_accounts = {t for t in loan_accounts if not account(t)}

    candidates = [r for r in live if r["txn_type"] == "debit" and usable(r) and r["id"] not in taken
                  and account(r["card_last4"]) and r["category"] in ("Self Transfer", "Transfer", LOAN_REPAYMENT)]

    def fits(d, c):
        return abs(d["amount"] - c["amount"]) < 0.005 and abs(_days(d["date"], c["date"])) <= LOAN_MAX_DAYS
    pairs = _one_to_one(candidates, loan_credits, fits)
    done = {d["id"] for d, _ in pairs}

    named = []
    for d in candidates:
        tail = _named_account(d["raw_description"])
        if d["id"] not in done and tail and any(_same_tail(tail, t) for t in loan_accounts):
            named.append(d)
    done |= {d["id"] for d in named}

    # amounts proven to be an EMI from a given own account
    confirmed = {(account(r["card_last4"]), round(r["amount"], 2)) for r in live
                 if r["txn_type"] == "debit" and r["category"] == LOAN_REPAYMENT and account(r["card_last4"])}
    confirmed |= {(account(d["card_last4"]), round(d["amount"], 2)) for d in [p[0] for p in pairs] + named}
    own_credits = [r for r in live if r["txn_type"] == "credit" and account(r["card_last4"])]

    def moved_to_own_account(d):
        return any(abs(c["amount"] - d["amount"]) < 0.005 and abs(_days(d["date"], c["date"])) <= LOAN_MAX_DAYS
                   and account(c["card_last4"]) != account(d["card_last4"]) for c in own_credits)
    by_amount = [d for d in candidates
                 if d["id"] not in done and d["category"] == "Self Transfer"
                 and not _named_account(d["raw_description"])
                 and (account(d["card_last4"]), round(d["amount"], 2)) in confirmed
                 and not moved_to_own_account(d)]
    return pairs, named, by_amount


def _one_to_one(left, right, fits):
    """Pairs (l, r) where r is l's only fitting partner and l is r's only fitting partner."""
    l_to_r = {l["id"]: [r for r in right if fits(l, r)] for l in left}
    r_to_l = defaultdict(list)
    for lid, rs in l_to_r.items():
        for r in rs:
            r_to_l[r["id"]].append(lid)
    by_id = {l["id"]: l for l in left}
    return [(by_id[lid], rs[0]) for lid, rs in l_to_r.items() if len(rs) == 1 and r_to_l[rs[0]["id"]] == [lid]]


def pair_rows(conn=None, dry_run=False):
    conn = conn or get_db()
    account = _accounts(conn)
    manual = _manual(conn)

    def usable(r):
        return r["confidence"] != "manual" and r["id"] not in manual

    live = conn.execute(f"SELECT {_ROW} FROM transactions WHERE is_duplicate = 0").fetchall()
    bare_debits = [r for r in live if r["txn_type"] == "debit" and _BARE_DEBIT.search(r["raw_description"] or "")
                   and account(r["card_last4"]) and usable(r)]
    bare_credits = [r for r in live if r["txn_type"] == "credit" and _BARE_CREDIT.search(r["raw_description"] or "")
                    and account(r["card_last4"]) and usable(r)]
    updates = []          # (merchant, category, kind or None, id)

    # own-transfer
    def own_fits(d, c):
        return (abs(d["amount"] - c["amount"]) < 0.005 and 0 <= _days(d["date"], c["date"]) <= 1
                and account(d["card_last4"]) != account(c["card_last4"]))
    own = _one_to_one(bare_debits, bare_credits, own_fits)
    for d, c in own:
        updates.append(("Own account transfer", "Self Transfer", None, d["id"]))
        updates.append(("Own account transfer", "Self Transfer", None, c["id"]))
    paired_debits = {d["id"] for d, _ in own}

    # returned: same account, same day — the debit came back
    def returned_fits(d, c):
        return (abs(d["amount"] - c["amount"]) < 0.005 and d["date"][:10] == c["date"][:10]
                and account(d["card_last4"]) == account(c["card_last4"]))
    # the debit may carry the owner's own label (that is what they are asked to decide for the
    # Rs 45,000 ones); its returned credit follows whatever that label is
    any_bare_debits = [r for r in live if r["txn_type"] == "debit" and _BARE_DEBIT.search(r["raw_description"] or "")
                       and account(r["card_last4"]) and r["id"] not in paired_debits]
    returned = _one_to_one(any_bare_debits,
                           [c for c in bare_credits if c["id"] not in {x["id"] for _, x in own}], returned_fits)
    for d, c in returned:
        updates.append((None, d["category"] or "Transfer", "reversal", c["id"]))
    paired_debits |= {d["id"] for d, _ in returned}

    # loan: EMIs paid by own-account transfer
    loan_pairs, loan_named, loan_by_amount = _loan_repayments(live, account, usable, paired_debits)
    loan_debits = [d for d, _ in loan_pairs] + loan_named + loan_by_amount
    for d in loan_debits:
        updates.append((LOAN_REPAYMENT, LOAN_REPAYMENT, None, d["id"]))
    for _, c in loan_pairs:
        updates.append(("Loan account", LOAN_REPAYMENT, None, c["id"]))
    paired_debits |= {d["id"] for d in loan_debits}
    bare_debits = [d for d in bare_debits if d["id"] not in paired_debits]

    # card-autopay
    active = set()
    for r in conn.execute("SELECT last4, pdf_card_last4 FROM cards WHERE active = 1"):
        active |= {str(v) for v in (r["last4"], r["pdf_card_last4"]) if v}
    card_payments = [r for r in live if r["txn_type"] == "credit" and str(r["card_last4"] or "") in active
                     and (r["kind"] == "bill_payment" or r["category"] == "Finance")]

    def autopay_fits(d, p):
        return abs(d["amount"] - p["amount"]) < 0.005 and 0 <= _days(d["date"], p["date"]) <= AUTOPAY_MAX_DAYS
    autopay = _one_to_one([d for d in bare_debits if d["id"] not in paired_debits], card_payments, autopay_fits)
    for d, _p in autopay:
        updates.append(("Card bill payment", "Finance", "bill_payment", d["id"]))

    # reversal
    by_ref = defaultdict(set)
    rows_by_id = {r["id"]: r for r in live}
    for r in conn.execute(f"SELECT {_ROW} FROM transactions WHERE txn_type = 'debit' AND ref_no IS NOT NULL "
                          "AND TRIM(ref_no) <> ''"):
        winner = r["matched_txn_id"] if r["is_duplicate"] and r["matched_txn_id"] else r["id"]
        if winner in rows_by_id:
            by_ref[r["ref_no"].strip().upper()].add(winner)
    reversals = 0
    used_debits = set()
    for c in sorted(live, key=lambda r: (r["date"], r["id"])):
        if c["txn_type"] != "credit" or c["kind"] != "reversal" or not (c["ref_no"] or "").strip() or not usable(c):
            continue
        debits = [rows_by_id[i] for i in by_ref.get(c["ref_no"].strip().upper(), ())]
        debits = [d for d in debits if d["amount"] + 0.005 >= c["amount"] and d["id"] not in used_debits
                  and 0 <= _days(d["date"], c["date"]) <= REVERSAL_MAX_DAYS]
        if len(debits) != 1 or not debits[0]["category"]:
            continue                   # (same category already: still marked paired, so it nets)
        updates.append((None, debits[0]["category"], None, c["id"]))
        used_debits.add(debits[0]["id"])
        reversals += 1

    changed = 0
    for merchant, category, kind, tid in updates:
        cur = conn.execute("SELECT merchant, category, kind, confidence FROM transactions WHERE id = ?", (tid,)).fetchone()
        new = (merchant or cur["merchant"], category, kind or cur["kind"], PAIRED)
        if (cur["merchant"], cur["category"], cur["kind"], cur["confidence"]) == new:
            continue
        changed += 1
        if not dry_run:
            conn.execute("UPDATE transactions SET merchant = ?, category = ?, kind = ?, confidence = ? WHERE id = ?",
                         (*new, tid))
    if not dry_run:
        conn.commit()
    return {
        "dry_run": dry_run,
        "own_transfers": len(own),
        "own_transfer_amount": round(sum(d["amount"] for d, _ in own), 2),
        "returned_debits": len(returned),
        "returned_amount": round(sum(d["amount"] for d, _ in returned), 2),
        "card_autopays": len(autopay),
        "card_autopay_amount": round(sum(d["amount"] for d, _ in autopay), 2),
        "reversals_aligned": reversals,
        "loan_repayments": len(loan_debits),
        "loan_repayment_amount": round(sum(d["amount"] for d in loan_debits), 2),
        "loan_by_evidence": {"loan_credit": [d["id"] for d, _ in loan_pairs],
                             "names_loan_account": [d["id"] for d in loan_named],
                             "confirmed_emi_amount": [d["id"] for d in loan_by_amount]},
        "rows_changed": changed,
    }
