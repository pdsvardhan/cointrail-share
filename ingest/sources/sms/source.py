"""SMS data source implementation."""
from typing import List
from sources.base import DataSource, ContentType, IngestedItem
from db import get_db
from modules.sms.receiver import get_sms_stats, classify_financial, is_non_transaction
from modules.sms.parser import parse_single_sms
from modules.categorizer import categorize


class SmsSource(DataSource):

    @property
    def source_name(self) -> str:
        return "sms"

    def sync(self, **kwargs) -> dict:
        """Process pending financial SMS into transactions — through the one SMS pipeline
        (modules.sms.processing, iter-18 HP1), never a private insert loop: the old loop here kept
        the amount+date+description dedup that merged genuine repeat payments and dropped unread
        money SMS silently."""
        from modules.sms.processing import process_pending
        return process_pending(get_db(), limit=kwargs.get("limit", 500))

    def classify(self, raw_data: dict) -> ContentType:
        """Classify an SMS into a content type."""
        body = raw_data.get("body", "")
        sender = raw_data.get("sender", "")

        if not classify_financial(sender, body):
            return ContentType.PROMOTIONAL

        if is_non_transaction(body):
            return ContentType.INFORMATIONAL

        body_lower = body.lower()

        # Payment confirmation
        if "payment" in body_lower and ("received" in body_lower or "credited" in body_lower) and "card" in body_lower:
            return ContentType.FINANCIAL_PAYMENT

        # Statement alert
        if "statement" in body_lower and ("total due" in body_lower or "generated" in body_lower):
            return ContentType.FINANCIAL_STATEMENT

        # Refund
        if "refund" in body_lower:
            return ContentType.FINANCIAL_TRANSACTION

        # Transaction
        if any(kw in body_lower for kw in ["debited", "spent", "paid", "sent rs", "withdrawn"]):
            return ContentType.FINANCIAL_TRANSACTION

        if any(kw in body_lower for kw in ["credited", "received"]):
            return ContentType.FINANCIAL_TRANSACTION

        return ContentType.UNKNOWN

    def process(self, raw_data: dict) -> List[IngestedItem]:
        """Parse an SMS into IngestedItems."""
        body = raw_data.get("body", "")
        parsed = parse_single_sms(body)
        if not parsed:
            return []

        merchant, category, confidence = categorize(parsed["description"])
        content_type = self.classify(raw_data)

        return [IngestedItem(
            source="sms",
            content_type=content_type,
            raw_text=body,
            amount=parsed.get("amount"),
            merchant=merchant,
            category=category,
            date=parsed.get("date"),
            card_last4=parsed.get("card_last4"),
            bank=parsed.get("bank"),
            txn_type=parsed.get("txn_type"),
            confidence=confidence,
            metadata={"pattern": parsed.get("pattern", ""), "source_type": parsed.get("source", "")},
        )]

    def get_stats(self) -> dict:
        return get_sms_stats()
