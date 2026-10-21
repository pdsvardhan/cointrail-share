import re
import os
from typing import Optional, Tuple


# Filenames that are NOT CC statements — skip these entirely
SKIP_FILENAME_PATTERNS = [
    r"Most Important Terms",
    r"Terms\s*&\s*Conditions",
    r"T&C",
    r"LDWK_",  # Unknown non-statement format
]


def is_skippable_pdf(pdf_path: str) -> bool:
    """Check if a PDF should be skipped entirely (T&C docs, junk, etc.)."""
    basename = os.path.basename(pdf_path)
    for pattern in SKIP_FILENAME_PATTERNS:
        if re.search(pattern, basename, re.IGNORECASE):
            return True
    return False


def detect_and_prepare(pdf_path: str) -> Tuple[Optional[str], str]:
    """Detect bank from PDF. Returns (bank, readable_path).
    If encrypted, tries bank-aware decryption.
    Returns (None, path) for skippable files."""

    if is_skippable_pdf(pdf_path):
        return None, pdf_path

    text = _extract_first_page(pdf_path)
    readable_path = pdf_path

    if not text:
        from modules.pdf.decrypt import is_encrypted, decrypt_pdf
        if is_encrypted(pdf_path):
            # Try to guess bank from filename for password priority
            bank_hint = _guess_bank_from_filename(pdf_path)
            decrypted = decrypt_pdf(pdf_path, bank_hint=bank_hint)
            if decrypted:
                text = _extract_first_page(decrypted)
                readable_path = decrypted

    if not text:
        return None, pdf_path

    return _match_bank(text), readable_path


def detect_bank(pdf_path: str) -> Optional[str]:
    bank, readable = detect_and_prepare(pdf_path)
    if readable != pdf_path and os.path.exists(readable):
        os.unlink(readable)   # the decrypted working copy is not needed to name the bank
    return bank


def _extract_first_page(path: str) -> Optional[str]:
    import pdfplumber   # imported here so the parsers' text-level code loads without it
    try:
        with pdfplumber.open(path) as pdf:
            return pdf.pages[0].extract_text() or ""
    except Exception:
        return None


def _guess_bank_from_filename(pdf_path: str) -> Optional[str]:
    """Guess bank from filename patterns."""
    basename = os.path.basename(pdf_path).lower()
    if "hdfc" in basename:
        return "hdfc"
    if "icici" in basename or "4315" in basename or "5241" in basename:
        return "icici"
    if "hsbc" in basename or "credit card statement" in basename:
        return "hsbc"
    if "sbi" in basename or "aurum" in basename or "cashback" in basename:
        return "sbi"
    if "axis" in basename or "airtel" in basename:
        return "axis"
    # ICICI filenames: 4315XXXXXXXX or 5241XXXXXXXX
    if re.match(r"(4315|5241)", basename):
        return "icici"
    # HDFC filenames: 5268XXXXXXXXXX, 4341XXXXXXXXXX, 6529XXXXXXXXXX
    if re.match(r"(5268|4341|6529)\d*[xX]", basename):
        return "hdfc"
    # SBI: the filename is the 16-digit card account number (no owner number hard-coded here)
    if re.match(r"\d{16}", basename):
        return "sbi"
    # HSBC: dated format YYYYMMDD.pdf (from HSBC statement emails)
    if re.match(r"20\d{6}\.pdf$", basename):
        return "hsbc"
    return None


def _match_bank(text: str) -> Optional[str]:
    patterns = {
        "hdfc": r"HDFC\s*BANK",
        "icici": r"ICICI\s*BANK",
        "sbi": r"State Bank|SBI\s*Card",
        "axis": r"Axis\s*Bank",
        "kotak": r"Kotak\s*Mahindra",
        "amex": r"American\s*Express",
        "hsbc": r"HSBC",
        "sc": r"Standard\s*Chartered",
        "indusind": r"IndusInd\s*Bank",
        "idfc": r"IDFC\s*FIRST|IDFC\s*Bank",
        "rbl": r"RBL\s*Bank",
        "yes": r"YES\s*BANK",
        "bob": r"Bank\s*of\s*Baroda",
        "federal": r"Federal\s*Bank",
        "au": r"AU\s*Small\s*Finance|AU\s*Bank",
        "onecard": r"OneCard|One\s*Card",
    }
    for bank, pattern in patterns.items():
        if re.search(pattern, text, re.IGNORECASE):
            return bank
    return None
