#!/usr/bin/env python3
"""
Build / extend the golden SMS corpus — iter-18 HP2.

READ-ONLY extractor. It reads the live sms + cards tables through `docker exec -i <container> python3 -`
over ssh, with the database opened `file:...?mode=ro` (the extractor below is piped on stdin; nothing is
copied to or left on the server), groups the bank / merchant SMS into distinct wordings, anonymises one
representative per wording and merges it into tests/corpus/sms_corpus.jsonl.

  python scripts/build_sms_corpus.py --forbidden ~/names.txt          # ssh user@your-server, container "ingest"
  python scripts/build_sms_corpus.py --forbidden ~/names.txt --from-json dump.json   # offline
  python scripts/build_sms_corpus.py --dump dump.json      # only fetch (the dump holds REAL data: keep it
                                                           # out of the repo and delete it after use)

Merging never touches an existing entry: hand-checked expectations survive every re-run. A wording that
is not yet covered is appended with  "expect": {"class": "review"}  — a human must read the message and
write the expectation (never copy parser output). Crafted entries ("origin": "crafted") are preserved.

Anonymisation (no real personal data may land in the corpus):
  * card / account suffixes -> fake suffixes, consistently (the same real suffix always maps to the same
    fake one; a 2-digit "XX11" maps to the last two digits of the fake for the card ending ...11);
  * people's names in payee / greeting positions -> fake names; VPAs -> fake handles (bank suffix kept);
  * phone numbers, reference numbers / UTRs / mandate ids / order ids / PNRs and any other identifier
    -> same-shape fakes; URLs and e-mail addresses -> example.invalid;
  * amounts, dates, times, merchant (business) descriptors and the wording itself are kept.
The output is then checked for the owner's name fragments, phone numbers and every real reference seen;
the script refuses to write if any survives.
"""
import argparse
import collections
import hashlib
import json
import os
import random
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "tests", "corpus", "sms_corpus.jsonl")
DEFAULT_CARDS = os.path.join(ROOT, "tests", "corpus", "cards.json")

EXTRACTOR = r'''
import json, sqlite3, sys
c = sqlite3.connect("file:/data/ingest.db?mode=ro", uri=True)
c.row_factory = sqlite3.Row
sms = [dict(r) for r in c.execute(
    "SELECT id, sender, body, phone_timestamp, received_at, is_financial FROM sms ORDER BY id")]
cards = [dict(r) for r in c.execute("SELECT bank, last4, card_name, active FROM cards ORDER BY id")]
json.dump({"sms": sms, "cards": cards}, sys.stdout)
'''

# Sender families whose wordings belong in the corpus (bank SMS, Pluxee, CRED, merchant refund notices).
FAMILIES = {
    "hdfc": ["HDFCBK", "HDFCBN"],
    "hsbc": ["HSBCIN", "HSBCIM"],
    "sbi": ["SBICRD", "MYSBIC", "SBICGV", "CBSSBI", "SBIUPI", "SBIPSG", "SBIINB", "SBIBNK", "SBYONO", "SBICHR"],
    "icici": ["ICICIT", "ICICIO", "ICICIB"],
    "axis": ["AXISBK", "AXISMR"],
    "pluxee": ["PLUXEE"],
    "cred": ["CREDIN"],
    "merchant": ["SWIGGY", "SWGTNG", "BLNKIT", "IRCTCI", "AIRINF"],
}
SENDER_FAMILY = {code: fam for fam, codes in FAMILIES.items() for code in codes}

# Never allowed in the output: the owner's and family members' name fragments. They are personal data
# themselves, so they are NOT kept in the repo: --forbidden names a file outside it (one fragment per
# line), default $SMS_CORPUS_FORBIDDEN_FILE. The script refuses to run without it.
FORBIDDEN_FRAGMENTS = []


def load_forbidden(path):
    if not path or not os.path.exists(path):
        sys.exit("a --forbidden file (owner / family name fragments, one per line) is required")
    with open(path, encoding="utf-8") as f:
        frags = [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]
    if not frags:
        sys.exit(f"{path} lists no name fragments")
    FORBIDDEN_FRAGMENTS[:] = frags
    return frags


# Fake people: first x last combinations, so no two real names share a fake and none carries digits.
_FAKE_FIRST = ["ASHA", "RAVI", "NEHA", "ARUN", "MEERA", "KIRAN", "VIJAY", "LATA", "SURESH", "ANITA", "MOHAN",
               "DEEPA", "RAJESH", "SUNITA", "PRAKASH", "KAVYA", "MANOJ", "REKHA", "SANJAY", "UMA"]
_FAKE_LAST = ["VERMA", "KUMAR", "SHARMA", "DAS", "NAIR", "RAO", "SINGH", "IYER", "BABU", "GUPTA", "LAL", "MENON",
              "PATIL", "JOSHI", "REDDY", "PILLAI", "YADAV", "BOSE", "MEHTA", "SHANKAR"]
FAKE_NAMES = [f"{_FAKE_FIRST[i % 20]} {_FAKE_LAST[(i + i // 20) % 20]}" for i in range(400)]

# Payees that are businesses, not people (kept verbatim). Lower-case substrings.
BUSINESS_HINTS = [
    "swiggy", "zomato", "blinkit", "instamart", "grofers", "cred", "amazon", "flipkart", "airtel", "netflix",
    "google", "apple", "spotify", "story tv", "atm", "salon", "naturals", "cafe", "bakers", "foods", "food",
    "store", "stores", "mart", "super", "market", "markets", "hotel", "restaurant", "cinema", "cinem", "general",
    "insuranc", "pvt", "ltd", "limited", "private", "llp", "services", "solutions", "tech", "india", "corner",
    "compass", "colony", "towers", "enterprises", "traders", "medical", "pharma", "hospital", "clinic", "rice",
    "snabbit", "rapido", "uber", "ola", "zepto", "dunzo", "bigbasket", "jio", "irctc", "railway", "bank",
    "cfms", "loan a/c", "a/c", "transfer", "interest", "credit", "debit", "salary", "request", "neft", "imps",
    "rtgs", "online", "jewels", "esds", "mohalla", "razorpay", "groww", "zerodha", "tea point", "tea", "juice",
    "bharatpe", "paytm", "phonepe", "petrol", "fuel", "dmart", "ratnadeep", "vijetha", "ushodaya",
    # salutations, not names
    "customer", "cardholder", "cardmember", "investor", "user", "guest", "sir", "madam",
    # brands that read like a single name
    "cloudflare", "proton", "handloom",
]


def norm_sender(s):
    s = (s or "").upper()
    m = re.match(r"^[A-Z]{2}-([A-Z0-9]+?)(?:-[STPG])?$", s)
    return m.group(1) if m else s


# --------------------------------------------------------------------------------------- fetching

def fetch_remote(host, container):
    cmd = ["ssh", "-o", "BatchMode=yes", host, f"docker exec -i {container} python3 -"]
    res = subprocess.run(cmd, input=EXTRACTOR.encode(), capture_output=True, check=True, timeout=300)
    return json.loads(res.stdout.decode("utf-8"))


# --------------------------------------------------------------------------------------- wordings

def _tokens(body):
    body = re.sub(r"https?://\S+", " URL ", body)
    # an alphanumeric run that contains a digit (masked card, reference, mandate hash) is one variable token
    return re.findall(r"\d[\d,./:-]*|[A-Za-z0-9*]*\d[A-Za-z0-9*@]*|[A-Za-z][A-Za-z'.*&/-]*|\S", body)


def wording_keys(rows):
    """Group rows into distinct wordings. Rows are first split into families by sender family + their
    first three words; inside a family, a token that occurs in fewer than 30% of the family's messages
    is a variable (merchant, payee) and digits are variables, so what remains is the template."""
    fams = collections.defaultdict(list)
    for r in rows:
        words = [t.lower() for t in _tokens(r["body"]) if t[0].isalpha() and not any(ch.isdigit() for ch in t) and t != "URL"]
        fams[r["family"] + "|" + " ".join(words[:3])].append(r)
    keyed = {}
    for fam, rs in fams.items():
        df = collections.Counter()
        for r in rs:
            df.update({t.lower() for t in _tokens(r["body"])})
        # in a family of one or two messages nothing can be told apart as "variable": keep every word
        thr = max(2, 0.3 * len(rs)) if len(rs) >= 3 else 1
        for r in rs:
            out = []
            for t in _tokens(r["body"]):
                x = "#" if any(ch.isdigit() for ch in t) else ("URL" if t == "URL" else ("*" if df[t.lower()] < thr else t.lower()))
                if out and out[-1] in ("*", "#") and x in ("*", "#"):
                    out[-1] = "*" if "*" in (out[-1], x) else "#"
                    continue
                out.append(x)
            keyed[r["id"]] = fam + "|" + " ".join(out)
    return keyed


# --------------------------------------------------------------------------------------- anonymising

class Anonymiser:
    def __init__(self, cards):
        self.maps = collections.defaultdict(dict)
        self.real_values = set()          # every real identifier replaced (checked absent at the end)
        self.card_names = {}
        real4 = sorted({c["last4"] for c in cards if re.fullmatch(r"\d{4}", c["last4"] or "")})
        for i, last4 in enumerate(real4):
            self._card(last4, i)
        self.cards = cards

    # suffixes ----------------------------------------------------------------
    def _card(self, last4, i=None):
        m = self.maps["card4"]
        if last4 not in m:
            i = len(m) if i is None else i
            hi = (31 + 7 * i) % 90 + 10
            lo = (53 + 11 * i) % 90 + 10          # 11 is coprime with 90: last-two-digit pairs never collide
            m[last4] = f"{hi:02d}{lo:02d}"
            self.maps["card2"][last4[-2:]] = m[last4][-2:]
        return m[last4]

    def suffix(self, digits):
        """Map a visible account/card suffix of 2-8 digits; only the last 4 (or fewer) carry identity."""
        if len(digits) >= 4:
            return "0" * (len(digits) - 4) + self._card(digits[-4:])
        if len(digits) == 3:
            m = self.maps["card3"]
            if digits not in m:
                m[digits] = f"{(len(m) * 37 + 301) % 900 + 100:03d}"
            return m[digits]
        if len(digits) == 2:
            m = self.maps["card2"]
            if digits not in m:
                m[digits] = f"{(len(m) * 13 + 41) % 90 + 10:02d}"
            return m[digits]
        return digits

    # opaque identifiers ------------------------------------------------------
    def fake(self, kind, value):
        """Same-shape fake for an identifier: letters stay letters (case kept), digits stay digits."""
        m = self.maps[kind]
        if value not in m:
            rng = random.Random(f"corpus-{kind}-{len(m)}")
            out = []
            for ch in value:
                if ch.isdigit():
                    out.append(str(rng.randrange(10)))
                elif ch.isalpha():
                    c = chr(rng.randrange(26) + 65)
                    out.append(c if ch.isupper() else c.lower())
                else:
                    out.append(ch)
            m[value] = "".join(out)
            if len(value) >= 5:
                self.real_values.add(value)
        return m[value]

    def name(self, value):
        key = " ".join(value.upper().split())
        m = self.maps["name"]
        if key not in m:
            m[key] = FAKE_NAMES[len(m) % len(FAKE_NAMES)]
            self.real_values.add(key)
        return m[key]

    def vpa(self, local, handle):
        keep = any(h in local.lower() for h in ("swiggy", "instamart", "blinkit", "googlepay", "zomato", "dominos",
                                                 "amazon", "flipkart", "paytmqr", "rzp", "bharatpe"))
        if keep and not re.search(r"\d{6,}", local):
            return f"{local}@{handle}"
        m = self.maps["vpa"]
        if local not in m:
            m[local] = f"payee{len(m) + 1}"
            self.real_values.add(local)
        return f"{m[local]}@{handle}"

    # body --------------------------------------------------------------------
    def _maybe_person(self, text):
        t = " ".join(text.split())
        low = t.lower()
        if any(f.lower() in low for f in FORBIDDEN_FRAGMENTS):
            return True
        if any(h in low for h in BUSINESS_HINTS) or "@" in t or re.search(r"\d", t):
            return False
        words = [w.strip(".") for w in re.split(r"[\s,]+", t) if w.strip(".")]
        return 1 <= len(words) <= 5 and all(w.isalpha() for w in words)

    PAYEE_SLOTS = [
        r"(is paid from HSBC account \S+ to )(.+?)( on \d)",
        r"(is (?:debited|credited) for INR [\d,.]+ on \S+ (?:towards|from) )(.+?)(\.\s|\.$| UPI)",
        r"(\nTo )([^\n]+?)(\s*\n)",
        r"(For IMPS -)(.+?)(- ?\d)",
        r"(trf to )(.+?)( Ref\s*no)",
        r"(Transferred to )(.+?)(\. Avl)",
        r"(deposit transfer from )(.+?)(\.-SBI)",
        r"(for transfer to )(.+?)( A[Cc] [Xx])",
        r"( to )(\w+(?: \w+)?)(\s+Ac\s+[Xx])",
        r"(sent to )(.+?)( [A-Z]{4}0[A-Z0-9]{6})",
        r"((?:SBIN|SBOI) A/c \*+\d+ )(.+?)( \.)",
        r"( of )([A-Z][A-Z ]+?)( (?:on \d|\.))",
        r"(A/c XXXXXXX\d+ )(?:of )?([A-Za-z ]+?)( on \d)",
        r"(Beneficiary addition request for )(.+?)( is| has)",
        r"(newly added beneficiary )(.+?)(\.)",
        r"(Dear (?:MR |MRS |MS )?)([A-Za-z ,]+?)(,)",
        r"(Hi )([A-Za-z]+,[A-Za-z ]+?)(, your)",
        r"( on )([A-Z][A-Z .]+?)(\.? Avl)",                 # ICICI UPI-on-card to a person
        r"(\n)([A-Z][A-Z ]+)(,(?:C\d|RLWL|WL|S\d|B\d))",     # IRCTC passenger line
        r"(towards )(.+?)( from A/c No)",
        r"(Hey )([^,]+?)(\.{0,3},)",                         # Blinkit "Hey <name>..., <friend> placed an order"
        r"(^|, |Hi, )([A-Za-z][A-Za-z ]+?)( (?:\(\S+\) )?(?:has )?(?:just )?placed an order)",
    ]

    # Labels whose value is an identifier that must contain a digit (refs, UTRs, order ids, PNRs …).
    _REF_LABELS = (
        r"Trxn\.?\s*Ref\.?\s*No\.?:?\s*|UPI\s*Ref\.?\s*(?:No\.?)?\s*|IMPS\s+Ref\s+no\.?\s*|UTR\s+|"
        r"reference\s+number\s+|Refno\s*|Ref#\s*|Ref\.?\s*No\.?\s*|Ref\s+|ref#\s*|with\s+ref\s+|RRN\s*:\s*|"
        r"by\s+UPI\s+|\(UPI\s+|UMRN:\s*|Transaction\s+ID:?\s*|order\s+ID\s*|order\s*#\s*|Order\s+|ORD|"
        r"PNR:?\s*|Txn\s+ID:?\s*-?|refund\s+ID:\s*|vide\s+Ref#\s*|Trip\s+ID:\s*|Service\s+Request\s+No\.?\s*|"
        r"SR\s+No\.\s*|KIN\)\s*|Awb\s*-?|AWB|Txn\s+#\s*|TransID\s*|Ref\.\s*No\.\s*|Application\s+No\.?\s*|"
        r"APPLICATION\s+NO\.")
    # Labels whose value is an opaque id that may be letters only (mandate / SI ids, codes).
    _ID_LABELS = (r"SiHub\s+ID\s*[:-]?\s*|Si-Hub\s+ID:\s*|SI\s+Hub\s+ID:\s*|Mandate\s+ID:\s*|"
                  r"Standing\s+Instructions?\s+|UMN:?\s*|e-Voucher\s+code\s+is\s+|code\s+|Use\s+")

    def body(self, text):
        """Anonymise one SMS body. Every replacement is parked behind a placeholder until the end, so a
        fake value is never re-read (and re-faked) by a later rule."""
        parked = []

        def park(value):
            parked.append(value)
            return f"{chr(0xE100 + len(parked) - 1)}"

        def sub(rx, fn, s, flags=0):
            return re.sub(rx, lambda m: park(fn(m)), s, flags=flags)

        t = text
        t = sub(r"https?://\S+", lambda m: "https://example.invalid/x", t)
        t = sub(r"\b(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:in|com|co|club|xyz|io|to|me|ly)/\S+",
                lambda m: "example.invalid/x", t, re.I)
        t = sub(r"[\w.*-]+@(?:gmail|yahoo|outlook|hotmail)\.com", lambda m: "user@example.invalid", t, re.I)
        # payees / greetings (the slots anchor on the surrounding wording, so run them first)
        def slot(m):
            v = self._payee_value(m.group(2))
            return m.group(1) + (park(v) if v != m.group(2) else v) + m.group(3)
        for rx in self.PAYEE_SLOTS:
            t = re.sub(rx, slot, t)
        # VPAs (keep the bank handle: the categorizer reads it) — before the name sweep, so a VPA that
        # contains a name is replaced whole
        t = sub(r"\b([\w.-]+)@([a-z]+)\b", lambda m: self.vpa(m.group(1), m.group(2)), t)
        # any remaining run of words containing an owner / family name fragment (the fragment match is
        # case-insensitive; the words that follow must be capitalised, so template words are not eaten)
        frag = "|".join(re.escape(f) for f in FORBIDDEN_FRAGMENTS)
        t = sub(r"\b(?:(?:Mr|Mrs|Ms)\.?\s+)?[A-Za-z]*(?i:" + frag + r")[A-Za-z]*(?:[ ,]+[A-Z][A-Za-z]*){0,3}",
                lambda m: self.name(m.group(0)), t)
        # labelled identifiers
        t = sub(r"\b[A-Z]{4}0[A-Z0-9]{6}\b", lambda m: m.group(0), t)        # IFSC codes are public: keep
        t = re.sub(r"(?i)\b(" + self._REF_LABELS + r")((?=[A-Za-z-]*\d)(?:\d{4,}(?=[A-Z][a-z])|[A-Za-z0-9-]{4,}))",
                   lambda m: m.group(1) + park(self.fake("ref", m.group(2))), t)
        def opaque(v):
            """An id, not an English word: has a digit, mixes cases, or is a long all-caps run."""
            return bool(re.search(r"\d", v) or (re.search(r"[a-z]", v) and re.search(r"[A-Z]", v))
                        or (v.isupper() and len(v) >= 6))
        t = re.sub(r"\b(" + self._ID_LABELS + r")([A-Za-z0-9][A-Za-z0-9-]{4,})\b",
                   lambda m: m.group(1) + park(self.fake("ref", m.group(2))) if opaque(m.group(2)) else m.group(0), t)
        # Android SMS-retriever app hash closing an OTP message
        t = re.sub(r"(\.\s+)([A-Za-z0-9+/]{11})\s*$", lambda m: m.group(1) + park(self.fake("ref", m.group(2))), t)
        # masked mobile / PAN / user ids
        t = sub(r"\bX{5,}(\d{4})\b(?=\s+through|\s+at\b)", lambda m: "XXXXXX" + self.fake("phone4", m.group(1)), t)
        t = sub(r"\bX{6}\d{3}[A-Z]\b", lambda m: "XXXXXX000X", t)
        t = sub(r"\b[A-Z][a-z]X{5}\d\b", lambda m: "UsXXXXX1", t)
        t = sub(r"\b\d{5}X{5}\b", lambda m: "90000XXXXX", t)                 # half-masked mobile number
        # phone numbers (10 digits, optional +91 / 91)
        t = sub(r"(?<![\d.,])(\+?91)?([6-9]\d{9})(?!\d)",
                lambda m: (m.group(1) or "") + self.fake("phone", m.group(2)), t)
        # HSBC account format 111-222***-001: prefix replaced, suffix mapped
        t = sub(r"\b\d{3}-\d{3}\*{3}-(\d{3})\b", lambda m: "123-456***-" + self.suffix(m.group(1)), t)
        # masked suffixes: XX9320 / xx9824 / *6611 / XXXXX004321 / x9434 / Xx4321 / ***4321
        t = re.sub(r"(?<![A-Za-z0-9])((?:[Xx]{1,12}|\*{1,4}))(\d{2,8})(?!\d)",
                   lambda m: m.group(1) + park(self.suffix(m.group(2))), t)
        # bare 4-digit card / account numbers in card contexts
        t = re.sub(r"(?i)\b((?:card ending(?: with)?|ending(?: with)?|A/C No|A/c|Ac|CC|DC|BLOCK(?: CC| DC| UPI)?|"
                   r"Card no\.?|Card)\s+)(\d{4})\b(?!-\d)",
                   lambda m: m.group(1) + park(self.suffix(m.group(2))), t)
        t = re.sub(r"(XXXX-)(\d{4})\b", lambda m: m.group(1) + park(self.suffix(m.group(2))), t)
        # any other long digit run that is not an amount (no currency right before, no decimals after)
        t = re.sub(r"(?<![\d.,])(?<!Rs\.)(?<!Rs )(?<!INR )(?<!INR)(\d{7,})(?!\d|,\d|\.\d)",
                   lambda m: park(self.fake("num", m.group(1))), t)
        return re.sub("(.)", lambda m: parked[ord(m.group(1)) - 0xE100], t)

    def _payee_value(self, val):
        if "@" in val:
            return val                                 # a VPA: handled by the VPA rule later
        honorific = re.match(r"^((?:Mrs|Mr|Ms)(?:\.\s*|\s+))(.*)$", val)
        if honorific and self._maybe_person(honorific.group(2)):
            return honorific.group(1) + self.name(honorific.group(2))
        if self._maybe_person(val):
            return self.name(val)
        return val

    def cards_config(self):
        out = []
        for c in self.cards:
            if not re.fullmatch(r"\d{4}", c["last4"] or ""):
                continue
            out.append({"bank": c["bank"], "last4": self._card(c["last4"]),
                        "card_name": c["card_name"] or "Card", "active": c["active"]})
        return out


def leak_check(lines, anon):
    """Refuse to write a corpus that still contains real personal data."""
    problems = []
    blob = "\n".join(lines)
    up = blob.upper()
    for frag in FORBIDDEN_FRAGMENTS:
        if frag in up:
            problems.append(f"name fragment {frag!r}")
    for v in anon.real_values:
        if len(v) >= 6 and v in blob:
            problems.append(f"real identifier {v[:3]}…")
    fakes = set()
    for kind, mapping in anon.maps.items():
        fakes.update(mapping.values())
    for m in re.finditer(r"(?<![\d.,])[6-9]\d{9}(?!\d)", blob):
        if not any(m.group(0) in f for f in fakes):
            problems.append(f"phone-like number {m.group(0)[:3]}…")
    return problems


# --------------------------------------------------------------------------------------- main

def load_corpus(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def build(data, out_path, cards_path):
    rows = []
    for r in data["sms"]:
        fam = SENDER_FAMILY.get(norm_sender(r["sender"]))
        if fam and (r["body"] or "").strip():
            r = dict(r, family=fam)
            rows.append(r)
    keys = wording_keys(rows)
    by_key = collections.defaultdict(list)
    for r in rows:
        by_key[keys[r["id"]]].append(r)

    anon = Anonymiser(data["cards"])
    existing = load_corpus(out_path)
    # A wording is covered when an existing entry carries its hash (the template key itself is not
    # stored: frequent payee names can survive in it) or the same anonymised body.
    covered = {e.get("wording") for e in existing} | {e["body"] for e in existing}

    counters = collections.Counter(e["id"].rsplit("-", 1)[0] for e in existing)
    added = []
    for key in sorted(by_key, key=lambda k: (k.split("|")[0], -len(by_key[k]), k)):
        rep = max(by_key[key], key=lambda r: r["id"])            # most recent wording instance
        # every representative is anonymised, covered or not, so fake values are allocated in the
        # same order on every run over the same data
        body = anon.body(rep["body"])
        wording = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        if wording in covered or body in covered:
            continue
        fam = rep["family"]
        counters[fam] += 1
        added.append({
            "id": f"{fam}-{counters[fam]:03d}",
            "origin": "live",
            "wording": wording,
            "sender": rep["sender"],
            "body": body,
            "received_at": rep["phone_timestamp"] or rep["received_at"],
            "occurrences": len(by_key[key]),
            "expect": {"class": "review"},
        })

    lines = [json.dumps(e, ensure_ascii=False) for e in existing + added]
    problems = leak_check([json.dumps([e["sender"], e["body"]], ensure_ascii=False) for e in added], anon)
    if problems:
        sys.exit("refusing to write, personal data survived anonymisation: " + "; ".join(sorted(set(problems))[:20]))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    if not os.path.exists(cards_path):
        with open(cards_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"cards": anon.cards_config()}, f, indent=2)
            f.write("\n")
    return {"existing": len(existing), "added": len(added), "wordings": len(by_key)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--host", default="user@your-server")
    ap.add_argument("--container", default="ingest")
    ap.add_argument("--from-json", help="use a previous --dump instead of fetching")
    ap.add_argument("--dump", help="fetch the live rows to this file and stop")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--cards", default=DEFAULT_CARDS)
    ap.add_argument("--forbidden", default=os.environ.get("SMS_CORPUS_FORBIDDEN_FILE"),
                    help="file of owner/family name fragments that must never reach the corpus (kept outside the repo)")
    args = ap.parse_args(argv)

    if args.from_json:
        with open(args.from_json, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = fetch_remote(args.host, args.container)
    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as f:
            json.dump(data, f)
        print(f"dumped {len(data['sms'])} sms rows to {args.dump} (REAL data — delete after use)")
        return
    load_forbidden(args.forbidden)
    print(json.dumps(build(data, args.out, args.cards)))


if __name__ == "__main__":
    main()
