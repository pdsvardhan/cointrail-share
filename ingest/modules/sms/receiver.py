"""
SMS receiver module — handles incoming SMS from phone (Tasker/SMS Forwarder)
and bulk imports from XML/JSON.
"""
import json
import os
from db import get_db
import re

FINANCIAL_SENDERS = [
    "hdfc", "icici", "sbi", "axis", "kotak", "hsbc",
    "indusind", "yes", "bob", "pnb", "canara", "union",
    "idbi", "federal", "rbl", "bandhan", "au small",
    "phonepe", "paytm", "gpay", "googlepay", "amazonpay",
    "cred", "slice", "jupiter", "fi.",
    # iter-18: Pluxee (meal card) fee deductions say only "Rs. 10.00 deducted from your Pluxee Card", one
    # keyword short of the >=2 rule, so they were stored as non-financial and never processed.
    "pluxee",
]

FINANCIAL_KEYWORDS = [
    "debited", "credited", "rs.", "rs ", "inr",
    "transaction", "spent", "paid", "received",
    "a/c", "account", "upi", "neft", "imps", "rtgs",
    "balance", "avl bal", "avbl bal",
    "emi", "autopay", "mandate",
]

# iter-18 H5: merchant-side refund / failed-payment notices. They announce that a refund WILL reach
# the card or account; the money movement itself arrives later as the bank's own credit SMS (HDFC
# "refunded by ... adjusted against", SBI "towards reversal/cashback", ICICI "Reversal of Rs"). Turning
# the notice into a credit too (the old `refund_generic` pattern did, with bank 'Unknown' and no card)
# nets the same refund twice. Every wording below was confirmed against real bodies in the corpus.
MERCHANT_REFUND_NOTICE_PATTERNS = [
    r"Refund of Rs\.?\s*[\d,.]+ has been (?:initiated|processed) for",    # Swiggy (sms 5315, 5510, 5244)
    r"Refund of (?:Rs\.?|INR)\s*[\d,.]+ received from .* is processed",   # Cashfree-powered merchant (3244)
    r"refund reference number for Rs",                                    # Swiggy (sms 5371, 4569, 2083)
    r"initiated a refund of Rs",                                          # Blinkit (sms 4963, 4848, 4528)
    r"\bwill\s+(?:get|be)\s+refunded\b",                                  # Swiggy/Toing not completed (5292, 4537,
                                                                          # 2226), IRCTC (5399, 3338), Airtel (4813, 3815)
]

NON_TRANSACTION_PATTERNS = [
    r"has been dispatched",
    r"will be unavailable",
    r"system upgrade",
    r"Claim Rs\.\d+ voucher",
    r"Congrats.*voucher",
    # iter-18 H5: "It's Cashback Time! ... Rs. 714 has been credited to your CASHBACK SBI Card account"
    # used to be filtered here. It is a real credit to the card (it reduces the bill) and is now parsed
    # as a cashback transaction; the same goes for Axis "Cashback of INR 141 has been credited".
    r"Amt Due.*Pay Instant",
    r"credit card bill.*has been generated",
    r"Declined!",
    r"locked out",
    r"OTP.*is\s+\d{4,8}",
    r"Flexipay EMI feature",
    r"plan has been.*activated",
    r"rewards redemption",
    r"has been closed on",
    # iter-16 C3 fix: was r"e-Mandate.*registered" — too broad. It also matched the
    # actual autopay DEBIT ("...against E-mandate (SiHub ID - X) registered by you at
    # merchant has been debited...") and so dropped 12 real recurring subscription
    # charges (Netflix, Hotstar, Google Play, JioCinema). The registration NOTICE says
    # "...e-Mandate ... has been registered with the following details"; the debit says
    # "registered by you ... has been debited". Anchor on "has been registered" so only
    # the registration notice is filtered and the debit stays a real transaction.
    r"e-Mandate.*has been registered",
    r"successfully logged in",
    r"statement.*is generated",
    r"E-statement.*has been mailed",
    r"verification code",
    r"activation code",
    r"Dear Customer.*limit.*increased",
    # iter-18 H5: was r"CONGRATULATIONS.*cashback", which also swallowed Axis "Congratulations! Cashback
    # of INR 141 has been credited to your ... Credit Card" — a real credit. The promos it was meant for
    # say "unlocked" (Airtel "You've unlocked flat Rs. 50 cashback", Axis "You have unlocked Amazon eVoucher").
    r"(?:You've|You have) unlocked",
    r"enquiry was made.*to check the validity",
    r"payment.*is due on.*Ignore if paid",
    r"payment.*is due on.*minimum amount",
    r"link to quickly pay",
    # Credit-card BILL PAYMENTS: money moving between the user's own account and their own
    # card, not spend. cards_service files these in cc_payments (deduping the two SMS the
    # bank sends per payment within +/-2 days), so they must stay out of transactions or the
    # same payment is counted twice — once as the card spend, once as the bill payment.
    r"PAYMENT OF Rs\.?\s*[\d,.]+\s*RECEIVED TOWARDS YOUR CREDIT CARD",
    r"Online Payment of Rs\.?\s*[\d,.]+.*credited to your card ending",
    # --- iter-16 C3: non-transaction categories the 27% "missed" measurement exposed ---
    # These carry an amount but describe no completed spend, so without a guard the
    # realtime /sms path can mint a false transaction from them. Each was confirmed
    # against real missed SMS in the corpus (see the C3 breakdown, 2026-08-20).
    r"was declined",                     # declined attempts ("... was declined as International...")
    r"has been declined",
    r"under maintenance",                # planned-outage notices
    r"will be under maintenance",
    r"services will be (?:un)?available",
    r"converted into EMI",               # a spend already counted, now re-described as EMI
    r"converted to EMI",
    r"Balance Transfer on EMI",          # marketing for the EMI facility
    r"beware of cyber fraud",            # fraud-awareness PSAs
    r"do not respond to any suspicious",
    r"invalid login attempt",            # security alerts
    r"login access will be locked",
    r"request for linking your .* to the Unified Payment Interface",  # UPI-linking requests
    r"Statement is sent to",             # statement-ready notices ("... Total of Rs X ... is due by")
    r"Statement.*is ready for viewing",
    r"total payment due on .* is INR",   # due-reminder alerts (not a payment)
    r"minimum payment due is",
    # Autopay PRE-NOTICE only ("your payment of INR X ... is due on DATE and will be
    # processed"). Deliberately NOT a bare "e-Mandate.*registered": the actual autopay
    # DEBIT ("Transaction of Rs.29 at Viacom18 against E-mandate ... registered by you")
    # is a real transaction and must keep parsing. The safety check caught this.
    r"payment of (?:INR|Rs\.?|USD)\s*[\d,.]+\s*at\s+.*is due on .* will be processed",

    # --- iter-18 HP1: noise the 2026-10-20 corpus exposed. Each carries an amount and a money verb,
    # so without a rule it would land in the parse quarantine; each was confirmed against real
    # bodies (sms ids cited) and the corpus test proves no real transaction wording matches. ---
    # OTPs printed digits-first: Axis "309535 is SECRET OTP for txn of INR 979.00" (5684),
    # "<#> 265488 is the OTP" (5691), SBI "564779 is the OTP for Trxn. of INR 287.00" (5696),
    # ICICI "296570 is One-Time Password for INR 433.00 transaction" (5406).
    r"^\W*(?:<#>\s*)?\d{4,8}\s+is\s+(?:the\s+|your\s+)?(?:SECRET\s+)?(?:OTP|One[- ]Time\s+Password)",
    # Future-tense debits: nothing has moved yet. HDFC "E-Mandate!\nRs.699.00 will be deducted on" (5197),
    # HDFC "AutoPay (E-mandate) Alert ... INR199.00 will be deducted" (195), SBI auto-debit "Outstanding
    # Amount of Rs 3454.84 will be debited" (4264), PhonePe collect "Rs.1 will be debited ... on
    # approving" (2919), ICICI SI pre-notice "to be debited from your ICICI Bank Credit Card" (766, 4157),
    # SBI "your SBI Card outstanding of Rs. 830.04 will get deducted on payment due date" (3153).
    r"\bwill (?:be|get) (?:debited|deducted)\b",
    r"\bto be debited from\b",
    # HDFC sends "AutoPay (E-mandate) Success! For NETFLIX Txn Amt:INR199.00" in the same minute as its
    # "Rs.199 without OTP/PIN HDFC Bank Card x7180 At NETFLIX" spend alert (253 / 254). The spend alert is
    # sent for every no-OTP charge (107, 323 have no Success twin), so it is the record and this the twin.
    r"AutoPay \(E-mandate\) Success",
    # Mandate / standing-instruction set-up and cancellation (no debit): HDFC "Mandate Set\nRs.699.00"
    # (5193), "UPI-Mandate:\nFor Rs. 15000.00" (5159), "UPI Mandate cancelled for Rs" (5184), "UPI mandate
    # to Story TV has been cancelled" (4092), "Your UPI-Mandate for Rs 99.00 is successfully created"
    # (2123); HSBC "UPI AutoPay Mandate for 1950.00 is successfully created" (1268); SBI "SI created in
    # your A/c ... for Rs 40,000.00" (3446); ICICI "activated Standing Instructions" (800) / "Standing
    # Instruction X ... has been cancelled" (4177).
    r"^\W*Mandate Set\b",
    r"UPI-Mandate:\s*For\b",
    r"mandate cancelled|mandate to .* has been cancelled|Mandate revokation",
    r"Mandate for (?:Rs\.?\s*)?[\d,.]+ is successfully created",
    r"SI created in your A/c|activated Standing Instructions|Standing Instruction \w+ on .* has been cancelled",
    # Merchant-side refund / failed-payment notices (H5) — see MERCHANT_REFUND_NOTICE_PATTERNS.
    *MERCHANT_REFUND_NOTICE_PATTERNS,
    r"\bhas failed\b|\bwas unsuccessful\b|\bwas not completed\b",   # Airtel (3815, 4813), Swiggy (5292),
                                                                    # SBI "Failure! SI ... has failed" (4186)
    # Merchant payment confirmations: the bank's own debit SMS is the record (Swiggy 4568, Airtel 5642 /
    # 737 / 130 / 2694, Airtel Payments Bank biller 2693, Hoi 2846, Blinkit coupon 4849, bill reminders 2978).
    r"Payment Confirmed! Your Swiggy Order",
    r"we have processed Rs\.?\s*[\d,.]+ for your Airtel",
    r"Recharge of INR [\d,.]+ is successful|recharge of Rs\.?\s*[\d,.]+ successfully credited",
    r"done via Credit Card for .* has been processed",
    r"payment for \d+ with Rs\.?\s*[\d,.]+ on .* is successful",
    r"Thanks for your purchase of Rs",
    r"special coupon worth Rs",
    r"Electricity bill of Rs",
    # Confirmations that the BENEFICIARY was credited for a transfer the debit SMS already recorded:
    # HSBC "your NEFT transaction with reference number ... has been credited to the SBIN A/c" (5603,
    # 2515); SBI "Your NEFT of Rs 5,50,000.00 with UTR ... credited to Beneficiary AC" (4051).
    r"your (?:NEFT|RTGS|IMPS) transaction with reference number .* has been credited to",
    r"Your (?:NEFT|RTGS|IMPS) of Rs\.?\s*[\d,.]+ with UTR .* credited to Beneficiary",
    # SBI's no-2FA notice for a recurring e-mandate charge. It always arrives alongside the e-mandate
    # debit SMS for the same charge (6 of 6 in the corpus: 676/675, 1970/1969, 2270/2269, 4145/4146,
    # 5085/5084, 5437/5436), and often with the amount withheld ("Trxn. of USD.00"), so it is the twin,
    # never the record.
    r"without effecting add'?n?l factor of authentication",
    r"\bUSD\s?\.00\s+(?:done|spent)",    # amount withheld by the bank (ICICI 3444); the statement carries it
    r"\bdeclined\b",                     # "is declined" (SBI 4408, ICICI 3428), "Txn Declined" (3213, 4574)
    r"there is a credit balance of Rs",  # SBI (3833)
    r"irregular by INR",                 # SBI loan arrears reminder (4774)
    r"\bEMI due\b",                      # SBI loan EMI reminder (5448)
    r"converted to \d+ EMIs",            # SBI EMI booking (4804)
    r"Convert your .* into EMI",
    r"consumed \d+% Credit Limit",       # SBI (3659)
    # Card renewal / T&C / charges / maintenance notices (HDFC 4243 / 4563 / 4698 / 1397, Axis 4778,
    # ICICI 2857 / 3279, SBI 911 / 1927, Axis 4265 / 1144, HSBC 2347 / 3827 / 1810).
    r"Renewal Alert|due for renewal|Renewed Card|renewed with new expiry|Stop renew",
    r"Terms and Conditions .* will be revised|will be revised w\.?e\.?f",
    r"changes to the features and charges|update for you regarding charges",
    r"Minimum Amount Due (?:\(MAD\) )?calculation",
    r"scheduled maintenance|maintenance downtime|system maintenance|temporarily unavailable|will be paused",
    r"loan facility|Pre-approved .*Loan",
    r"paid back as cashback",
    r"has been accepted and (?:category limit has been set|is set at)",   # SBI card limit change (1371, 1372)
    # SBI loan disbursal ("Loan of Rs.X disbursed from Loan A/c ... and credit to A/c No XXXXX300001"): a
    # liability event whose credit leg need not be the owner's account — sms 2773 disbursed Rs 70,00,000
    # straight to a third party's account. Recording it as a credit invents income; what the owner then
    # does with money that did land in their account is recorded by the debit SMS. Owner review item.
    r"Loan of (?:Rs\.?|INR)\s*[\d,.]+ disbursed",
    r"order no\.\s*\S+ for Rs\.?\s*[\d,.]+ has been accepted",           # merchant order ack (Hoi 2847)
    r"pre-?authori[sz]ation",            # card holds, not spends
    r"Avl Bal in A/c .* is Rs|Available limit on your .* is Rs",
]

_NOISE_RES = [re.compile(p, re.IGNORECASE) for p in NON_TRANSACTION_PATTERNS]
_MERCHANT_NOTICE_RES = [re.compile(p, re.IGNORECASE) for p in MERCHANT_REFUND_NOTICE_PATTERNS]


def noise_reason(body):
    """The NON_TRANSACTION_PATTERNS entry a body matches, or None."""
    for rx in _NOISE_RES:
        if rx.search(body or ""):
            return rx.pattern
    return None


def is_non_transaction(body):
    """Check if SMS looks financial but isn't an actual transaction."""
    return noise_reason(body) is not None


def is_merchant_refund_notice(body):
    """A merchant's 'refund initiated / will be refunded' notice — not a money movement (iter-18 H5)."""
    return any(rx.search(body or "") for rx in _MERCHANT_NOTICE_RES)


def classify_financial(sender, body):
    """Determine if an SMS is financial."""
    s = sender.lower().replace("-", "").replace(" ", "")
    b = body.lower()
    if any(fs in s for fs in FINANCIAL_SENDERS):
        return True
    return sum(1 for kw in FINANCIAL_KEYWORDS if kw in b) >= 2


def store_sms(sender, body, phone_timestamp=None, raw_json=None):
    """Store a single SMS and return its ID."""
    conn = get_db()
    is_fin = 1 if classify_financial(sender, body) else 0

    # Dedup by sender + body + timestamp
    if phone_timestamp:
        existing = conn.execute(
            "SELECT id FROM sms WHERE sender = ? AND body = ? AND phone_timestamp = ?",
            (sender, body, phone_timestamp)
        ).fetchone()
        if existing:
            return {"id": existing["id"], "is_financial": bool(is_fin), "status": "duplicate"}

    cur = conn.execute(
        "INSERT INTO sms (sender, body, phone_timestamp, is_financial, raw_json) VALUES (?, ?, ?, ?, ?)",
        (sender, body, phone_timestamp, is_fin, json.dumps(raw_json) if raw_json else None)
    )
    conn.commit()
    return {"id": cur.lastrowid, "is_financial": bool(is_fin), "status": "stored"}


def get_unprocessed_financial(limit=100):
    """Get financial SMS that haven't been processed into transactions yet.

    is_processed: 0 pending, 1 done, 2 quarantined (iter-18 HP1). Quarantined rows are retried by
    modules.sms.processing.retry_quarantine, not on every pass."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, sender, body, phone_timestamp, received_at FROM sms "
        "WHERE is_financial = 1 AND is_processed = 0 ORDER BY received_at ASC, id ASC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def mark_processed(ids):
    """Mark SMS as processed after transaction extraction."""
    if not ids:
        return
    conn = get_db()
    ph = ",".join("?" * len(ids))
    conn.execute(f"UPDATE sms SET is_processed = 1 WHERE id IN ({ph})", ids)
    conn.commit()


def get_sms_stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM sms").fetchone()["c"]
    financial = conn.execute("SELECT COUNT(*) as c FROM sms WHERE is_financial = 1").fetchone()["c"]
    processed = conn.execute("SELECT COUNT(*) as c FROM sms WHERE is_processed = 1").fetchone()["c"]
    quarantined = conn.execute("SELECT COUNT(*) as c FROM sms WHERE is_processed = 2").fetchone()["c"]
    return {"total": total, "financial": financial, "processed": processed, "quarantined": quarantined}
