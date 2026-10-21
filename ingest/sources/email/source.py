"""Email/Gmail data source implementation."""
from typing import List
from sources.base import DataSource, ContentType, IngestedItem
from db import get_db
from modules.email.classifier import classify_email_financial, FINANCIAL_SENDER_DOMAINS


class EmailSource(DataSource):

    @property
    def source_name(self) -> str:
        return "email"

    def sync(self, **kwargs) -> dict:
        """Sync emails from Gmail API."""
        from modules.email.gmail import sync_emails
        query = kwargs.get("query", "newer_than:2d")
        return sync_emails(query=query)

    def classify(self, raw_data: dict) -> ContentType:
        """Classify an email into a content type."""
        sender_email = (raw_data.get("sender_email") or "").lower()
        subject = (raw_data.get("subject") or "").lower()
        snippet = (raw_data.get("body_snippet") or "").lower()

        if not classify_email_financial(sender_email, subject, snippet):
            if any(kw in subject for kw in ["offer", "sale", "discount", "cashback", "voucher"]):
                return ContentType.PROMOTIONAL
            return ContentType.UNKNOWN

        # CC statement
        if "statement" in subject and any(kw in subject for kw in ["credit card", "card", "monthly"]):
            return ContentType.FINANCIAL_STATEMENT

        # Payment confirmation
        if "payment" in subject and any(kw in subject for kw in ["received", "confirmation", "successful"]):
            return ContentType.FINANCIAL_PAYMENT

        # Order/receipt
        if any(kw in subject for kw in ["order confirmed", "order delivered", "your order", "order from"]):
            return ContentType.RECEIPT

        # Subscription
        if any(kw in subject for kw in ["subscription", "renewal", "plan", "premium"]):
            return ContentType.SUBSCRIPTION

        # Transaction alert
        if any(kw in subject for kw in ["transaction", "debited", "credited", "spent"]):
            return ContentType.FINANCIAL_TRANSACTION

        # Refund
        if "refund" in subject:
            return ContentType.FINANCIAL_TRANSACTION

        return ContentType.INFORMATIONAL

    def process(self, raw_data: dict) -> List[IngestedItem]:
        """Classify email and return as IngestedItem (emails don't produce transactions directly)."""
        content_type = self.classify(raw_data)
        return [IngestedItem(
            source="email",
            content_type=content_type,
            raw_text=(raw_data.get("subject") or "") + " " + (raw_data.get("body_snippet") or ""),
            metadata={
                "gmail_id": raw_data.get("gmail_id"),
                "sender_email": raw_data.get("sender_email"),
                "subject": raw_data.get("subject"),
                "has_attachments": raw_data.get("has_attachments", False),
            },
        )]

    def get_stats(self) -> dict:
        from modules.email.gmail import get_email_stats
        return get_email_stats()
