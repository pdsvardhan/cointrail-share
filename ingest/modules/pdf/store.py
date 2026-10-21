"""
Statement PDF -> transaction rows + the bank's printed bill (iter-18).

Every statement line is written through txn_store.insert_transaction with
    source='card', source_ref='<file basename>#<line_index>', context='card'
so a line's identity is its position in its statement: re-parsing the same file is a no-op, and
two identical lines in one statement (two Rs 280 coffees on one day) are two transactions. The
old per-file `seen` sets and the DB check on amount+date+raw_description collapsed them into one.

Re-downloaded statements. Gmail delivers some statements twice and the downloader saves the
second copy under a new name ('X.pdf' and 'X_1.pdf'; 24 documents had byte-identical copies in
/data/statements on 2026-10-20). Before a
line is inserted, the same card's existing card-source rows on the same (date, amount, txn_type)
are counted per originating file; when another file already holds that line, it is not inserted
again. The count is per file and per key, so a statement with two identical lines against a copy
holding two is fully matched, while one holding one (rows written by the pre-iter-18 parser, which
collapsed duplicates) still lets the second, genuinely missing line in. Which rows count:
  - rows from a DIFFERENT file (source_ref '<other>#n') only with the same raw_description — the
    same statement parsed by the same parser prints the same text;
  - rows with no source_ref (written before iter-18) on (card, date, amount, txn_type) alone: the
    old parsers' descriptions differ (ICICI kept the reward-points column, HDFC stored '+'), so
    text cannot be compared, and such a row is by definition an earlier parse of a statement;
  - rows from THIS file whose line index moved (a different pdfplumber build) likewise.
reparse_statements() — the one-off post-deploy run — uses (card, date, amount, txn_type) with
any description for every row, so the improved parsers only add what was genuinely missing.
A pre-iter-18 row the old parsers filed under the wrong card (HDFC filename map, ICICI add-on
sections) is moved to the right card instead of being duplicated there (_misfiled_legacy_row).

One-off repairs of rows the old parsers mislabelled: fix_hdfc_plus_rows (M2) and
fix_legacy_payment_rows (bill payments stored as ordinary card rows).

The bill itself (HP5): when a statement prints its Total Amount Due and Payment Due Date they are
recorded through services.cards_service.record_bank_statement (CARDS workstream contract 1),
against the REAL card last4 — statement identifiers (SBI's account number, ICICI add-on
section headers) are resolved through the cards table, never a hard-coded map.

Iteration 203: payment lines are never transactions, but they are card-bill PAYMENTS
(record_payment_lines, E8), and the account summary the statement prints is stored beside the sums
of its own lines so the reconcile compares like with like (record_summary, E12).
"""
import glob
import os
import re
from collections import Counter, defaultdict

from config import STATEMENTS_DIR
from modules import txn_store
from modules.pdf.detect import detect_and_prepare, is_skippable_pdf


def statement_files(directory=None):
    """Statement PDFs on disk (decrypted working copies excluded), in name order so an
    original 'X.pdf' is processed before its re-download 'X_1.pdf'."""
    directory = directory or STATEMENTS_DIR
    paths = glob.glob(os.path.join(directory, "*.pdf")) + glob.glob(os.path.join(directory, "*.PDF"))
    return sorted({p for p in paths if ".decrypted" not in p})


# --- card identity -------------------------------------------------------------------------------

def known_card(conn, bank, identifier):
    """The real card last4 for an identifier a statement printed, or None if no card matches.

    Matches, in order: the card's own last4 (same bank), a statement alias in
    cards.pdf_card_last4 (an SBI statement names the card by its account number), and — for the 2-digit
    suffix SBI prints on the statement face ('XX11') — the bank's single active card ending so."""
    ident = (identifier or "").strip()
    if not ident:
        return None
    rows = conn.execute("SELECT bank, last4, pdf_card_last4, active FROM cards").fetchall()
    same_bank = [r for r in rows if (r["bank"] or "").lower() == (bank or "").lower()]
    for r in same_bank:
        if r["last4"] == ident:
            return r["last4"]
    for r in rows:
        if r["pdf_card_last4"] and r["pdf_card_last4"] == ident:
            return r["last4"]
    if len(ident) < 4:
        cands = [r for r in same_bank if r["active"] and (r["last4"] or "").endswith(ident)]
        if len(cands) == 1:
            return cands[0]["last4"]
    return None


def resolve_card(conn, bank, identifier):
    """Real card last4 for a statement identifier; the identifier itself when no card matches."""
    return known_card(conn, bank, identifier) or (identifier or "").strip()


def _card_bank_name(conn, last4, fallback):
    row = conn.execute("SELECT bank FROM cards WHERE last4 = ? ORDER BY active DESC LIMIT 1", (last4,)).fetchone()
    return row["bank"] if row else fallback


def _card_family(conn, last4):
    """The card's last4 plus every statement alias that maps to it (rows may carry either)."""
    fam = {last4}
    for r in conn.execute("SELECT pdf_card_last4 FROM cards WHERE last4 = ? AND pdf_card_last4 IS NOT NULL",
                          (last4,)):
        fam.add(r["pdf_card_last4"])
    return sorted(f for f in fam if f)


# --- line matching ---------------------------------------------------------------------------------

def _origin(source_ref):
    """Which file a card row came from: the basename before '#', or None for pre-iter-18 rows."""
    if not source_ref or "#" not in source_ref:
        return None
    return source_ref.rsplit("#", 1)[0]


class DryRunPlan:
    """What a dry run would have written so far, so later files see it as if it had been
    (a re-download of a statement must not count as 'missing' a second time)."""

    def __init__(self):
        self.lines = defaultdict(Counter)    # (family, key) -> {basename: n}
        self.refiled = set()                 # legacy row ids that would have been re-filed


class _LineMatcher:
    """Decides, line by line, whether a statement line is already stored (see module docstring)."""

    def __init__(self, conn, basename, any_description, plan=None):
        self.conn = conn
        self.basename = basename
        self.any_description = any_description
        self.planned = (plan or DryRunPlan()).lines
        self.used = Counter()

    def _capacity(self, family, key, description):
        date, amount, txn_type = key
        marks = ",".join("?" * len(family))
        rows = self.conn.execute(
            f"SELECT source_ref, raw_description FROM transactions WHERE source = 'card' "
            f"AND card_last4 IN ({marks}) AND date = ? AND ABS(amount - ?) < 0.005 AND txn_type = ?",
            (*family, date, amount, txn_type),
        ).fetchall()
        legacy, per_file = 0, Counter()
        for r in rows:
            origin = _origin(r["source_ref"])
            if origin is None:
                legacy += 1
                continue
            if (origin != self.basename and not self.any_description
                    and (r["raw_description"] or "") != description):
                continue
            per_file[origin] += 1
        for origin, n in self.planned[(tuple(family), key)].items():
            per_file[origin] += n
        # A file's copy of a statement = the pre-iter-18 rows it matched + the lines it added
        # itself, so legacy rows add to (not compete with) the best-covered file.
        return legacy + (max(per_file.values()) if per_file else 0)

    def decide(self, family, key, description, source_ref):
        """'exists' (this very line is stored), 'matched' (another copy is) or 'insert'."""
        fkey = (tuple(family), key)
        if txn_store.find_existing(self.conn, "card", source_ref):
            self.used[fkey] += 1
            return "exists"
        if self.used[fkey] < self._capacity(family, key, description):
            self.used[fkey] += 1
            return "matched"
        self.used[fkey] += 1
        return "insert"

    def plan(self, family, key):
        self.planned[(tuple(family), key)][self.basename] += 1


# --- storing one statement ---------------------------------------------------------------------

def _record_statement(conn, bank, card_last4, stmt, dry_run):
    """Hand the printed bill to the cards service (contract 1). A statement with nothing due
    prints no due date ('Nil' / 'NO PAYMENT REQUIRED'); there is no bill to record then."""
    if stmt.total_due is None or stmt.due_date is None or not card_last4:
        return {"recorded": False, "reason": "no printed total/due date" if card_last4 else "unknown card"}
    info = {"card_last4": card_last4, "total_due": stmt.total_due, "due_date": stmt.due_date.isoformat(),
            "min_due": stmt.min_due, "source": f"pdf-{bank}"}
    if dry_run:
        return {"recorded": False, "would_record": info}
    from services import cards_service
    cards_service.record_bank_statement(conn, card_last4, stmt.total_due, stmt.due_date.isoformat(),
                                        source=f"pdf-{bank}", min_due=stmt.min_due)
    return {"recorded": True, **info}


def statement_card(conn, bank, stmt):
    """The real card a statement belongs to: its printed/filename identifier, else the short
    suffix printed on its face (SBI 'XX11'), else the identifier as printed."""
    return (known_card(conn, bank, stmt.card_last4) or known_card(conn, bank, stmt.card_suffix)
            or (stmt.card_last4 or "").strip())


def line_card(conn, bank, txn, statement_card_last4):
    """ICICI lists add-on cards under their own header: a line keeps the card it was made on
    when that is a card we track, else it belongs to the statement's card."""
    return known_card(conn, bank, txn.card_last4) or statement_card_last4


def line_key(txn):
    return (txn.date, round(float(txn.amount), 2), "credit" if txn.is_credit else "debit")


_TIME_PREFIX = re.compile(r"^\d{2}:\d{2}(?::\d{2})?\s+")


def _misfiled_legacy_row(conn, bank, family, key, description, exclude, statement_card_last4):
    """A pre-iter-18 row for exactly this line that the old parser filed under ANOTHER card.

    Two ways that happened, both recognised by bank + date + amount + direction:
      - The 2025 HDFC layout printed "Card No: NNNN NNXX XXXX NNNN", which the old text regex did
        not read, so the old parser fell back to a hard-coded filename map that sent the Tata Neu
        statement to the Millennia card. Same description, after the HH:MM:SS time the old parser
        left in front of it.
      - ICICI add-on lines (the Amex add-on's section of a Sapphiro statement) were all stored
        under the statement's card; the line now belongs to the add-on card, and the old row is
        the one on the statement's card (its description still carried the reward points).
    Only a single unambiguous candidate is taken."""
    date, amount, txn_type = key
    rows = conn.execute(
        "SELECT id, card_last4, raw_description FROM transactions WHERE source = 'card' "
        "AND source_ref IS NULL AND LOWER(COALESCE(bank, '')) = ? AND date = ? "
        "AND ABS(amount - ?) < 0.005 AND txn_type = ?",
        ((bank or "").lower(), date, amount, txn_type),
    ).fetchall()
    want = (description or "").strip()
    add_on_line = bool(statement_card_last4) and statement_card_last4 not in family
    cands = [r for r in rows
             if (r["card_last4"] or "") not in family and r["id"] not in exclude
             and (_TIME_PREFIX.sub("", (r["raw_description"] or "").strip()) == want
                  or (add_on_line and r["card_last4"] == statement_card_last4))]
    return cands[0] if len(cands) == 1 else None


def _refile(conn, txn_id, card_last4, source_ref):
    """Move a pre-iter-18 row to the card its statement line belongs to; it becomes that line."""
    conn.execute(
        "UPDATE transactions SET card_last4 = ?, source_ref = ?, "
        "card_id = (SELECT id FROM cards WHERE last4 = ? AND active = 1) WHERE id = ?",
        (card_last4, source_ref, card_last4, txn_id))
    conn.commit()


def _stored_on(conn, family, key):
    date, amount, txn_type = key
    marks = ",".join("?" * len(family))
    return conn.execute(
        f"SELECT 1 FROM transactions WHERE source = 'card' AND card_last4 IN ({marks}) AND date = ? "
        f"AND ABS(amount - ?) < 0.005 AND txn_type = ? LIMIT 1", (*family, date, amount, txn_type),
    ).fetchone() is not None


def store_statement(conn, pdf_path, bank, stmt, *, any_description=False, dry_run=False, plan=None):
    """Write one parsed statement's lines and record its printed bill. Returns per-file stats.

    Per line: 'exists' (this line is stored), 'matched' (another copy of it is), 'refiled' (a
    pre-iter-18 copy sat on the wrong card and is moved to the right one) or inserted."""
    basename = os.path.basename(pdf_path)
    card = statement_card(conn, bank, stmt)
    plan = plan or DryRunPlan()
    matcher = _LineMatcher(conn, basename, any_description, plan)
    counts = Counter()
    inserted, refiled = [], []
    for t in sorted(stmt.transactions, key=lambda x: x.line_index):
        lcard = line_card(conn, bank, t, card)
        family = _card_family(conn, lcard) if lcard else [""]
        key = line_key(t)
        txn_type = key[2]
        source_ref = f"{basename}#{t.line_index}"
        decision = matcher.decide(family, key, t.description, source_ref)
        if decision == "insert" and lcard:
            wrong = _misfiled_legacy_row(conn, bank, family, key, t.description, plan.refiled, card)
            if wrong is not None:
                decision = "refiled"
                refiled.append({"id": wrong["id"], "date": t.date, "amount": t.amount,
                                "from_card": wrong["card_last4"], "to_card": lcard})
                if dry_run:
                    plan.refiled.add(wrong["id"])
                    matcher.plan(family, key)
                else:
                    _refile(conn, wrong["id"], lcard, source_ref)
        counts[decision] += 1
        if decision != "insert":
            continue
        if dry_run:
            matcher.plan(family, key)
            inserted.append({"date": t.date, "amount": t.amount, "txn_type": txn_type,
                             "card": lcard, "description": t.description, "kind": t.pattern_kind})
            continue
        res = txn_store.insert_transaction(
            conn, amount=t.amount, date=t.date, source="card", raw_description=t.description,
            source_ref=source_ref, bank=_card_bank_name(conn, lcard, bank), card_last4=lcard,
            txn_type=txn_type, currency=t.currency or "INR", original_amount=t.original_amount,
            fx_estimated=0, pattern_kind=t.pattern_kind, context="card",
        )
        inserted.append({"id": res["id"], "date": t.date, "amount": t.amount, "txn_type": txn_type,
                         "card": lcard, "description": t.description, "kind": res.get("kind")})
    # Payment lines are never inserted, but an old parser may have stored one under the wrong
    # card (the 2025 Tata Neu statement's TELE TRANSFER CREDIT sat on the Millennia card).
    for t in stmt.payments:
        lcard = line_card(conn, bank, t, card)
        if not lcard:
            continue
        family = _card_family(conn, lcard)
        key = line_key(t)
        if _stored_on(conn, family, key):
            continue
        wrong = _misfiled_legacy_row(conn, bank, family, key, t.description, plan.refiled, card)
        if wrong is None:
            continue
        counts["refiled"] += 1
        refiled.append({"id": wrong["id"], "date": t.date, "amount": t.amount,
                        "from_card": wrong["card_last4"], "to_card": lcard, "payment": True})
        if dry_run:
            plan.refiled.add(wrong["id"])
        else:
            _refile(conn, wrong["id"], lcard, f"{basename}#{t.line_index}")
    pay = record_payment_lines(conn, bank, card, basename, stmt, dry_run=dry_run, plan=plan)
    try:
        statement = _record_statement(conn, bank, card, stmt, dry_run)
    except Exception as e:  # the lines are already stored; report the bill failure, don't hide it
        statement = {"recorded": False, "error": f"{type(e).__name__}: {e}"}
    if not dry_run:
        try:
            statement["summary_on"] = record_summary(conn, card, stmt)
        except Exception as e:
            statement["summary_error"] = f"{type(e).__name__}: {e}"
    return {
        "file": basename, "bank": bank, "card_last4": card,
        "transactions": len(stmt.transactions), "payments_skipped": len(stmt.payments),
        "payments_recorded": pay["recorded"], "payments_would_record": pay["would_record"],
        "stored": counts["insert"], "exists": counts["exists"], "matched_existing": counts["matched"],
        "refiled": counts["refiled"], "statement": statement, "summary": stmt.summary(),
        "inserted": inserted, "refiled_rows": refiled,
    }


# --- the printed account summary (iteration 203 E12) ---------------------------------------------

def statement_summary(stmt):
    """What the statement printed about its own lines, beside what its lines add up to, so the
    reconcile can compare like with like. A bounced payment's reversal debit is on the bank's charges
    side, its payment on the credits side. None when the PDF printed no account summary."""
    if stmt.purchases is None or stmt.payments_credits is None:
        return None
    charges = sum(float(t.amount) for t in stmt.transactions if not t.is_credit)
    charges += sum(float(t.amount) for t in stmt.payments if not t.is_credit)
    credits = sum(float(t.amount) for t in stmt.transactions if t.is_credit)
    credits += sum(float(t.amount) for t in stmt.payments if t.is_credit)
    emi = sum(float(t.amount) for t in stmt.transactions if not t.is_credit and
              txn_store.derive_kind("debit", None, t.description, "card", t.pattern_kind) == "emi_principal")
    return {"printed_previous": stmt.previous_balance, "printed_purchases": round(stmt.purchases, 2),
            "printed_credits": round(stmt.payments_credits, 2), "lines_charges": round(charges, 2),
            "lines_credits": round(credits, 2), "lines_emi_principal": round(emi, 2)}


def record_summary(conn, card_last4, stmt):
    """Store statement_summary() on the card's statement row; returns the row id or None."""
    summary = statement_summary(stmt)
    if summary is None or not card_last4 or not stmt.period_end:
        return None
    from services import cards_service
    sid = cards_service.record_statement_summary(conn, card_last4, stmt.period_end.isoformat(), summary)
    conn.commit()
    return sid


# --- payment lines (iteration 203 E8) -------------------------------------------------------------

BOUNCE_DAYS = 3


def _bounced(payments):
    """Indexes of payment lines that cancel out: a payment credit and the same-amount debit the bank
    posted when it bounced ("DIRECT DEBIT PAYMENT(ACH)" + "INSUFFICIENT FUNDS"), paired 1:1."""
    from datetime import date as _d
    out, debits = set(), [i for i, t in enumerate(payments) if not t.is_credit]
    for i, t in enumerate(payments):
        if not t.is_credit:
            continue
        for j in debits:
            u = payments[j]
            if j not in out and abs(float(u.amount) - float(t.amount)) < 0.005 and \
                    abs((_d.fromisoformat(u.date) - _d.fromisoformat(t.date)).days) <= BOUNCE_DAYS:
                out |= {i, j}
                break
    return out


def record_payment_lines(conn, bank, statement_card_last4, basename, stmt, dry_run=False, plan=None):
    """A statement's "PAYMENT RECEIVED" lines are card-bill payments like a bank SMS or a CRED email:
    a payment made with no SMS, no email and not through CRED appeared only here, and the card stayed
    "overdue" until 45 days after the statement. Each payment credit goes through
    cards_service.record_card_payment (source 'statement', stmt_ref '<file>#<line>'), which treats it as
    the same payment as an SMS / email / CRED report of the same card and amount within 4 days and keeps
    two lines of one statement two payments. A bounced payment and its reversal debit record nothing.
    Returns {'recorded', 'would_record', 'lines'}."""
    from services import cards_service as cs
    bounced = _bounced(stmt.payments)
    planned = (plan or DryRunPlan()).lines
    recorded = would = 0
    lines = []
    claimed = set()
    for i, t in enumerate(stmt.payments):
        if not t.is_credit or i in bounced:
            continue
        lcard = line_card(conn, bank, t, statement_card_last4)
        if not lcard:
            continue
        stmt_ref = f"{basename}#{t.line_index}"
        amount = round(float(t.amount), 2)
        if dry_run:
            card = cs._resolve_card(conn, lcard) or cs._resolve_card(conn, lcard, active_only=False)
            if not card:
                continue
            group = cs._group_last4s(conn, card["last4"])
            key = ("payment", tuple(group), amount)
            # a line this dry run already counted from ANOTHER file is the same line in a re-download
            copies = [d for d, f in planned[key] if f != basename
                      and abs(_days_between(d, t.date)) <= cs.STATEMENT_PAYMENT_DAYS]
            same = cs._same_payment(conn, group, amount, t.date, stmt_ref=stmt_ref, exclude_ids=claimed)
            if same is not None:
                claimed.add(same["id"])            # the real run tags it with this line
                continue
            if copies:
                continue
            planned[key][(t.date, basename)] += 1
            would += 1
            lines.append({"card": card["last4"], "date": t.date, "amount": amount, "description": t.description})
            continue
        res = cs.record_card_payment(conn, lcard, amount, t.date, "statement", stmt_ref=stmt_ref,
                                     raw_text=t.description)
        if res["status"] == "inserted":
            recorded += 1
            lines.append({"card": res["card_last4"], "date": t.date, "amount": amount, "description": t.description})
    if not dry_run:
        conn.commit()
    return {"recorded": recorded, "would_record": would, "lines": lines}


def _days_between(a, b):
    from datetime import date as _d
    return (_d.fromisoformat(b) - _d.fromisoformat(a)).days


def payments_from_statements(conn, dry_run=True, directory=None):
    """Iteration 203 E8 history step: record the payment lines of every statement PDF on disk.
    Lines already evidenced by an SMS / email / CRED / bank-debit payment only add their stmt_ref."""
    plan = DryRunPlan()
    totals = Counter()
    lines = []
    for path in statement_files(directory):
        bank, stmt = parse_file(path)
        if bank is None:
            continue
        card = statement_card(conn, bank, stmt)
        res = record_payment_lines(conn, bank, card, os.path.basename(path), stmt, dry_run=dry_run, plan=plan)
        totals["files"] += 1
        totals["payment_lines"] += len(stmt.payments)
        totals["recorded"] += res["recorded"]
        totals["would_record"] += res["would_record"]
        lines += res["lines"]
        # E12: the same pass stores each statement's printed account summary for the reconcile
        if statement_summary(stmt) is not None:
            totals["summaries"] += 1
            if not dry_run and record_summary(conn, card, stmt):
                totals["summaries_attached"] += 1
    return {"dry_run": dry_run, **totals, "lines": lines}


def parse_file(pdf_path):
    """Detect the bank and parse. Returns (bank, Statement), or (None, status dict) for a file
    that is not a parseable card statement. Any decrypted working copy is removed."""
    from modules.pdf.parsers import get_parser

    basename = os.path.basename(pdf_path)
    if is_skippable_pdf(pdf_path):
        return None, {"file": basename, "status": "skipped", "reason": "non-statement PDF"}
    bank, readable = detect_and_prepare(pdf_path)
    try:
        if not bank:
            return None, {"file": basename, "status": "unknown_bank", "transactions": 0}
        try:
            parser = get_parser(bank)
        except ValueError:
            return None, {"file": basename, "status": "unsupported_bank", "bank": bank, "transactions": 0}
        return bank, parser.parse_statement(readable)
    except Exception as e:
        # no "bank" key: process-unparsed leaves the attachment unparsed so it is retried
        return None, {"file": basename, "status": "error", "error": f"{type(e).__name__}: {e}"}
    finally:
        if readable != pdf_path and os.path.exists(readable):
            os.unlink(readable)


def parse_and_store(pdf_path, conn):
    """The routine path (upload, parse-path, process-all, hourly process-unparsed)."""
    bank, stmt = parse_file(pdf_path)
    if bank is None:
        return stmt
    try:
        return store_statement(conn, pdf_path, bank, stmt)
    except Exception as e:
        return {"file": os.path.basename(pdf_path), "status": "error", "error": f"{type(e).__name__}: {e}"}


# --- one-off repairs (post-deploy) --------------------------------------------------------------

def reparse_statements(conn, bank=None, dry_run=True, directory=None):
    """Re-parse every statement PDF with the iter-18 parsers WITHOUT duplicating stored rows.

    A parsed line matching an existing card-source row on (card, date, amount, txn_type) — any
    description — is skipped; only genuinely missing lines are inserted (ICICI rows the old parser
    dropped, SBI wrapped/undated/EMI lines, refunds the old amount+date+description check merged
    into their purchase, ...). Pre-iter-18 rows filed under the wrong card are moved to the right
    one. Statements' printed totals and due dates are recorded. With dry_run=True nothing is
    written and the result lists what would be.

    `unmatched_existing`: pre-iter-18 card rows, within the periods of the statements on disk,
    that no parsed line of any statement accounts for (e.g. the HDFC '+' rows — see
    fix_hdfc_plus_rows — or payment lines older parsers kept). Reported, never changed."""
    plan = DryRunPlan()
    files, totals = [], Counter()
    covered = defaultdict(Counter)       # card -> line key -> most copies seen in one statement
    periods = defaultdict(list)          # card -> [(start, end)]
    for path in statement_files(directory):
        file_bank, stmt = parse_file(path)
        if file_bank is None:
            if not bank:
                totals[stmt["status"]] += 1
                files.append({k: stmt[k] for k in ("file", "status", "error") if k in stmt})
            continue
        if bank and file_bank != bank.lower():
            continue
        res = store_statement(conn, path, file_bank, stmt, any_description=True, dry_run=dry_run,
                              plan=plan)
        per_card = defaultdict(Counter)
        for t in stmt.transactions:
            per_card[line_card(conn, file_bank, t, res["card_last4"])][line_key(t)] += 1
        for c, keys in per_card.items():
            for k, n in keys.items():
                covered[c][k] = max(covered[c][k], n)
        if stmt.period_start and stmt.period_end:
            span = (stmt.period_start.isoformat(), stmt.period_end.isoformat())
            for c in set(per_card) | {res["card_last4"]}:
                periods[c].append(span)
        for k in ("transactions", "payments_skipped", "stored", "exists", "matched_existing", "refiled"):
            totals[k] += res[k]
        totals["files"] += 1
        files.append(res)
    unmatched = _unmatched_existing(conn, covered, periods, plan.refiled if dry_run else set())
    totals["unmatched_existing"] = len(unmatched)
    return {"dry_run": dry_run, "bank": bank, "totals": dict(totals), "files": files,
            "unmatched_existing": unmatched}


def _unmatched_existing(conn, covered, periods, ignore_ids):
    out = []
    for card, spans in sorted(periods.items()):
        if not card:
            continue
        family = _card_family(conn, card)
        marks = ",".join("?" * len(family))
        rows = conn.execute(
            f"SELECT id, date, amount, txn_type, raw_description, category FROM transactions "
            f"WHERE source = 'card' AND source_ref IS NULL AND card_last4 IN ({marks}) ORDER BY date, id",
            family,
        ).fetchall()
        left = Counter(covered[card])
        for r in rows:
            if r["id"] in ignore_ids or not any(s <= r["date"] <= e for s, e in spans):
                continue
            key = (r["date"], round(float(r["amount"]), 2), r["txn_type"])
            if left[key] > 0:
                left[key] -= 1
                continue
            out.append({"id": r["id"], "card_last4": card, "date": r["date"], "amount": r["amount"],
                        "txn_type": r["txn_type"], "category": r["category"],
                        "raw_description": r["raw_description"]})
    return out


def fix_hdfc_plus_rows(conn, dry_run=False):
    """iter-18 M2: re-label HDFC statement rows the old parser stored as DEBITS described '+'.

    They are card-bill payments whose description wrapped around the date line (see
    parsers/hdfc.py): 2026-04-06 Rs 4,950 and 2026-06-20 Rs 15,336 on the Tata Neu card were counted as
    spend. They become what they are — credit / bill_payment / Finance. merge_rule (and any
    duplicate link) is left untouched. Returns the number of rows changed (or that would be)."""
    where = ("source = 'card' AND LOWER(bank) = 'hdfc' AND TRIM(raw_description) = '+' "
             "AND txn_type = 'debit'")
    if dry_run:
        return conn.execute(f"SELECT COUNT(*) AS n FROM transactions WHERE {where}").fetchone()["n"]
    cur = conn.execute(
        f"UPDATE transactions SET txn_type = 'credit', kind = 'bill_payment', category = 'Finance' WHERE {where}")
    conn.commit()
    return cur.rowcount


def fix_legacy_payment_rows(conn, dry_run=False):
    """Label card-bill payments that pre-iter-18 parsers stored as ordinary card rows.

    Same root cause as M2, other wordings: HDFC "BPPY PYMT ..." (2026-08-12 Rs 6,773, Tata Neu,
    stored Uncategorized), SBI "DIRECT DEBIT PAYMENT(ACH)" credits (stored as category EMI) and
    the bounced auto-debit that reversed one ("INSUFFICIENT FUNDS", a Rs 3,454.84 debit stored as
    Transfer). The iter-18 parsers set all of these aside as payments; rows already stored keep
    their direction but become category Finance / kind bill_payment, so they are neither spend
    nor refunds. Rows already labelled so are left alone; the HDFC '+' rows are
    fix_hdfc_plus_rows' job. Returns the number of rows changed (or that would be)."""
    from modules.pdf.parsers.base import is_payment

    rows = conn.execute(
        "SELECT id, raw_description, category, kind FROM transactions WHERE source = 'card' "
        "AND source_ref IS NULL AND TRIM(COALESCE(raw_description, '')) <> '+'"
    ).fetchall()
    ids = [r["id"] for r in rows
           if is_payment(_TIME_PREFIX.sub("", (r["raw_description"] or "").strip()))
           and (r["category"] != "Finance" or r["kind"] != "bill_payment")]
    if dry_run or not ids:
        return len(ids)
    conn.executemany(
        "UPDATE transactions SET category = 'Finance', kind = 'bill_payment' WHERE id = ?",
        [(i,) for i in ids])
    conn.commit()
    return len(ids)
