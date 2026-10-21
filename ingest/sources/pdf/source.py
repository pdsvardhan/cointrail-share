"""PDF CC statement data source implementation."""
import os
from typing import List
from sources.base import DataSource, ContentType, IngestedItem
from config import STATEMENTS_DIR
from db import get_db
from modules.categorizer import categorize


class PdfSource(DataSource):

    @property
    def source_name(self) -> str:
        return "pdf"

    def sync(self, **kwargs) -> dict:
        """Parse all CC statement PDFs in the statements directory.

        Same path as POST /pdf/process-all (iter-18): lines go through txn_store with a
        '<file>#<line>' source_ref and statement bills are recorded — see modules/pdf/store.py."""
        from modules.pdf.store import parse_and_store, statement_files

        conn = get_db()
        results = [parse_and_store(pdf_path, conn) for pdf_path in statement_files(STATEMENTS_DIR)]
        conn.commit()
        return {"processed": len(results), "results": results}

    def classify(self, raw_data: dict) -> ContentType:
        """PDFs are always CC statements."""
        return ContentType.FINANCIAL_STATEMENT

    def process(self, raw_data: dict) -> List[IngestedItem]:
        """Parse a single PDF into IngestedItems (nothing is stored)."""
        pdf_path = raw_data.get("path", "")
        if not pdf_path or not os.path.exists(pdf_path):
            return []

        from modules.pdf.store import known_card, parse_file, statement_card

        bank, stmt = parse_file(pdf_path)
        if bank is None:
            return []
        conn = get_db()
        card = statement_card(conn, bank, stmt)
        items = []
        for t in stmt.transactions:
            merchant, category, confidence = categorize(t.description, context="card")
            items.append(IngestedItem(
                source="pdf",
                content_type=ContentType.FINANCIAL_STATEMENT,
                raw_text=t.description,
                amount=t.amount,
                merchant=merchant,
                category=category,
                date=t.date,
                card_last4=known_card(conn, bank, t.card_last4) or card,
                bank=bank,
                txn_type="credit" if t.is_credit else "debit",
                confidence=confidence,
                metadata={"pdf_file": os.path.basename(pdf_path), "line_index": t.line_index},
            ))
        return items

    def get_stats(self) -> dict:
        from modules.pdf.decrypt import is_encrypted
        from modules.pdf.store import statement_files
        pdfs = statement_files(STATEMENTS_DIR)
        encrypted = sum(1 for p in pdfs if is_encrypted(p))
        return {"total": len(pdfs), "encrypted": encrypted, "readable": len(pdfs) - encrypted}
