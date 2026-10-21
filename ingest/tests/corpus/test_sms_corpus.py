"""
Golden SMS corpus — iter-18 HP2.

sms_corpus.jsonl holds sample bank-SMS wordings (HDFC, SBI, ICICI, Axis, HSBC, Pluxee, CRED and merchant
refund/payment notices) with fake card numbers and hand-written expectations. This public copy is a small
subset of the author's private corpus; add your own bank's wordings the same way. Every entry goes through modules.sms.processing.process_sms_row on
a throw-away database and must land in the expected class with the expected transaction fields. A parser
or noise-rule change that breaks any real wording fails here; a new bank wording means one new pattern
plus one corpus entry. Entries marked {"class": "review"} are wordings whose correct treatment is the
owner's call; they are processed (must not crash) but not asserted.

cards.json holds the fake cards the corpus' card numbers map to (with their real card names, so the
"CASHBACK SBI Card" lookup resolves exactly as in production).
"""
import collections
import json
import os

from modules.sms.processing import process_sms_row

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "sms_corpus.jsonl"), encoding="utf-8") as _f:
    CORPUS = [json.loads(line) for line in _f if line.strip()]
with open(os.path.join(HERE, "cards.json"), encoding="utf-8") as _f:
    CARDS = json.load(_f)["cards"]

CLASSES = {"transaction", "noise", "bill_payment", "statement", "non_financial", "quarantined", "review"}
EXACT_FIELDS = ("txn_type", "kind", "currency", "card_last4", "date", "ref_no")


def _check(conn, entry, sms_id, out):
    exp = entry["expect"]
    problems = []
    if out["class"] != exp["class"]:
        return [f"class {out['class']!r} ({out.get('reason')}) != expected {exp['class']!r}"]
    if exp["class"] != "transaction":
        return problems
    t = conn.execute("SELECT * FROM transactions WHERE source = 'sms' AND source_ref = ?", (str(sms_id),)).fetchone()
    if t is None:
        return ["no transaction row written"]
    if "amount" in exp and abs(float(t["amount"]) - float(exp["amount"])) > 0.005:
        problems.append(f"amount {t['amount']} != {exp['amount']}")
    if "original_amount" in exp and abs(float(t["original_amount"] or 0) - float(exp["original_amount"])) > 0.005:
        problems.append(f"original_amount {t['original_amount']} != {exp['original_amount']}")
    for field in EXACT_FIELDS:
        if field in exp and (t[field] or "") != exp[field]:
            problems.append(f"{field} {t[field]!r} != {exp[field]!r}")
    if "merchant_contains" in exp:
        hay = f"{t['merchant'] or ''} {t['raw_description'] or ''}".lower()
        if exp["merchant_contains"].lower() not in hay:
            problems.append(f"merchant {t['merchant']!r} / {t['raw_description']!r} lacks {exp['merchant_contains']!r}")
    if (t["currency"] or "INR") != "INR" and not t["fx_estimated"]:
        problems.append("foreign amount not flagged fx_estimated")
    return problems


def test_corpus_is_well_formed():
    ids = [e["id"] for e in CORPUS]
    assert len(ids) == len(set(ids)), "duplicate corpus ids"
    for e in CORPUS:
        assert e["expect"]["class"] in CLASSES, e["id"]
        assert e["sender"] and e["body"] and e["received_at"], e["id"]
        if e["expect"]["class"] == "transaction":
            assert {"txn_type", "amount", "date"} <= set(e["expect"]), e["id"]


def test_corpus_covers_every_family():
    fams = collections.Counter(e["id"].rsplit("-", 1)[0] for e in CORPUS)
    assert len(CORPUS) >= 40
    for fam in ("hdfc", "sbi", "icici", "axis", "hsbc", "pluxee", "cred", "merchant"):
        assert fams[fam] > 0, fam
    classes = collections.Counter(e["expect"]["class"] for e in CORPUS)
    for cls in ("transaction", "noise", "bill_payment", "statement", "non_financial"):
        assert classes[cls] > 0, cls


def test_golden_corpus(conn, make_card):
    for c in CARDS:
        make_card(c["bank"], c["last4"], name=c["card_name"], active=c["active"])
    failures = []
    for entry in CORPUS:
        cur = conn.execute(
            "INSERT INTO sms (sender, body, phone_timestamp, is_financial) VALUES (?, ?, ?, 1)",
            (entry["sender"], entry["body"], entry["received_at"]))
        sms_id = cur.lastrowid
        row = conn.execute("SELECT * FROM sms WHERE id = ?", (sms_id,)).fetchone()
        out = process_sms_row(conn, row)
        if entry["expect"]["class"] == "review":
            continue
        problems = _check(conn, entry, sms_id, out)
        if problems:
            failures.append(f"{entry['id']}: {'; '.join(problems)}\n    {entry['body'][:160]!r}")
    assert not failures, f"{len(failures)} corpus entries failed:\n" + "\n".join(failures)
