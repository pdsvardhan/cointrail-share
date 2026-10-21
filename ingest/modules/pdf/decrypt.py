"""Decrypt password-protected Indian bank CC statement PDFs.
Bank-aware password matching from environment variables."""
import os
import tempfile
from typing import Optional, List

import pikepdf

from config import get_pdf_passwords


def get_passwords_for_bank(bank: str = None) -> List[str]:
    """Get passwords in priority order: bank-specific first, then all others."""
    bank_passwords = get_pdf_passwords()
    passwords = []

    # Bank-specific passwords first
    if bank:
        bank_lower = bank.lower()
        for key, pws in bank_passwords.items():
            if bank_lower in key or key in bank_lower:
                passwords.extend(pws)

    # Then all other passwords
    for key, pws in bank_passwords.items():
        for pw in pws:
            if pw not in passwords:
                passwords.append(pw)

    return passwords


def get_passwords() -> List[str]:
    """Get all available passwords (legacy compat)."""
    return get_passwords_for_bank(None)


def decrypt_pdf(pdf_path: str, bank_hint: str = None) -> Optional[str]:
    """Try to decrypt a PDF with known passwords. Returns the path of a decrypted working copy
    (the caller deletes it) or None.

    The copy goes to the temp directory, not next to the statement: copies written beside the
    originals were left behind in the statements folder whenever a caller failed (iter-18: 20
    '.decrypted.pdf' files sat in /data/statements on 2026-10-20), and a re-parse must not write
    to the data directory at all."""
    passwords = get_passwords_for_bank(bank_hint)
    if not passwords:
        return None

    for password in passwords:
        try:
            pdf = pikepdf.open(pdf_path, password=password)
        except pikepdf.PasswordError:
            continue
        except Exception:
            continue
        fd, decrypted_path = tempfile.mkstemp(prefix="stmt-", suffix=".decrypted.pdf")
        os.close(fd)
        try:
            pdf.save(decrypted_path)
        except Exception:
            os.unlink(decrypted_path)
            continue
        finally:
            pdf.close()
        return decrypted_path
    return None


def is_encrypted(pdf_path: str) -> bool:
    """Check if a PDF is password-protected."""
    try:
        pdf = pikepdf.open(pdf_path)
        pdf.close()
        return False
    except pikepdf.PasswordError:
        return True
    except Exception:
        return False
