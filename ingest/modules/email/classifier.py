"""
Email financial classifier.
Determines if an email is finance-related based on sender domain and content.
"""

FINANCIAL_SENDER_DOMAINS = {
    # Banks
    "hdfcbank.com": "HDFC Bank",
    "hdfcbank.net": "HDFC Bank",
    "icicibank.com": "ICICI Bank",
    "axisbank.com": "Axis Bank",
    "sbi.co.in": "SBI",
    "onlinesbi.com": "SBI",
    # RBI moved Indian banks to the restricted .bank.in zone in 2026 (alerts@hdfcbank.bank.in,
    # credit_cards@icici.bank.in, alerts@axis.bank.in, cbsalerts.sbi@alerts.sbi.bank.in); none of
    # these matched, so their alerts were stored as non-financial with only a snippet (iter-18 A3 13.14).
    "hdfcbank.bank.in": "HDFC Bank",
    "icici.bank.in": "ICICI Bank",
    "axis.bank.in": "Axis Bank",
    "axisbankmail.bank.in": "Axis Bank",
    "sbi.bank.in": "SBI",
    "kotak.bank.in": "Kotak",
    "hsbc.bank.in": "HSBC",
    "hsbc.co.in": "HSBC",
    "hsbc.com": "HSBC",
    "kotak.com": "Kotak",
    "kotakbank.com": "Kotak",
    "indusind.com": "IndusInd",
    "yesbank.in": "Yes Bank",
    "federalbank.co.in": "Federal Bank",
    "rblbank.com": "RBL Bank",
    "aubank.in": "AU Small Finance",
    "idfcfirstbank.com": "IDFC First",
    # Credit card
    "sbicard.com": "SBI Card",
    # Payment/fintech
    "phonepe.com": "PhonePe",
    "paytm.com": "Paytm",
    "cred.club": "CRED",
    "razorpay.com": "Razorpay",
    "cashfree.com": "Cashfree",
    "simpl.co": "Simpl",
    "slice.vip": "Slice",
    "jupiter.money": "Jupiter",
    "fi.money": "Fi Money",
    # Investments
    "kuvera.in": "Kuvera",
    "groww.in": "Groww",
    "zerodha.net": "Zerodha",
    "zerodha.com": "Zerodha",
    "camsonline.com": "CAMS",
    "kfintech.com": "KFintech",
    "bseindia.com": "BSE",
    "nseindia.com": "NSE",
    "motilaloswal.com": "Motilal Oswal",
    "angelone.in": "Angel One",
    # Insurance
    "godigit.com": "Digit Insurance",
    "acko.com": "Acko",
    "maxlifeinsurance.com": "Max Life",
    "hdfclife.com": "HDFC Life",
    "iciciprulife.com": "ICICI Prudential",
    "starhealth.in": "Star Health",
    "policybazaar.com": "PolicyBazaar",
    "coverfox.com": "Coverfox",
    # E-commerce (helps with cross-referencing)
    "amazon.in": "Amazon",
    "amazon.com": "Amazon",
    "flipkart.com": "Flipkart",
    "myntra.com": "Myntra",
    "swiggy.in": "Swiggy",
    "toingit.com": "Toing",
    "toingit.in": "Toing",
    "zomato.com": "Zomato",
    "makemytrip.com": "MakeMyTrip",
    "goibibo.com": "Goibibo",
    "irctc.co.in": "IRCTC",
    # Tax
    "incometax.gov.in": "Income Tax",
    "gst.gov.in": "GST",
    # Utilities
    "tsspdcl.in": "TSSPDCL",
    "apspdcl.in": "APSPDCL",
    "bescom.co.in": "BESCOM",
}

FINANCIAL_SUBJECT_KEYWORDS = [
    "statement", "credit card", "account summary",
    "transaction", "payment received", "payment confirmation",
    "emi ", "auto debit", "mandate",
    "mutual fund", "sip ", "nav ", "folio",
    "insurance", "premium", "policy",
    "salary", "pay slip", "payslip",
    "recharge", "bill payment",
    "order confirmed", "order delivered",  # e-commerce for cross-ref
    "refund", "cashback",
    "investment", "portfolio",
    "tax", "form 16", "form 26as", "tds",
]


BANK_IN_SUFFIX = ".bank.in"   # only RBI-licensed banks can register under bank.in


def _domain_label(domain):
    """Label for a sender domain or any parent of it ('alerts.sbi.co.in' -> sbi.co.in -> 'SBI'),
    or None. Exact-domain matching missed every bank subdomain (alerts.sbi.co.in carries the
    CBSSBI account alerts; custcomm.icicibank.com, mail.hsbc.co.in ...)."""
    parts = (domain or "").lower().strip(".").split(".")
    for i in range(len(parts) - 1):
        label = FINANCIAL_SENDER_DOMAINS.get(".".join(parts[i:]))
        if label:
            return label
    d = ".".join(parts)
    if d.endswith(BANK_IN_SUFFIX) and len(parts) >= 3:
        return parts[-3].capitalize() + " Bank"
    return None


def classify_email_financial(sender_email, subject, body_snippet=""):
    """Determine if an email is financial. Returns True/False."""
    if not sender_email:
        return False

    # Check sender domain (and its parents)
    domain = sender_email.lower().split("@")[-1] if "@" in sender_email else ""
    if _domain_label(domain):
        return True

    # Check subject keywords
    subj_lower = (subject or "").lower()
    if any(kw in subj_lower for kw in FINANCIAL_SUBJECT_KEYWORDS):
        return True

    # Check body snippet
    snippet_lower = (body_snippet or "").lower()
    financial_body_keywords = ["rs.", "inr", "credited", "debited", "transaction"]
    if sum(1 for kw in financial_body_keywords if kw in snippet_lower) >= 2:
        return True

    return False


def get_sender_label(sender_email):
    """Get a human-readable label for a known financial sender."""
    if not sender_email or "@" not in sender_email:
        return None
    return _domain_label(sender_email.lower().split("@")[-1])
