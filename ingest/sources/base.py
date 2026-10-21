"""
Abstract base class for data sources.
Each source (SMS, Email, PDF, future Telegram/Notifications) implements this interface.
"""
from abc import ABC, abstractmethod
from typing import List, Optional
from dataclasses import dataclass, field
from enum import Enum


class ContentType(str, Enum):
    """Classification of ingested content."""
    FINANCIAL_TRANSACTION = "financial_transaction"   # UPI, card spend, bank transfer
    FINANCIAL_STATEMENT = "financial_statement"       # CC statement email/PDF
    FINANCIAL_PAYMENT = "financial_payment"           # CC payment confirmation
    RECEIPT = "receipt"                               # Order confirmation, delivery receipt
    SUBSCRIPTION = "subscription"                     # Subscription renewal/charge
    INFORMATIONAL = "informational"                   # Account alerts, balance updates, OTPs
    PROMOTIONAL = "promotional"                       # Offers, marketing
    UNKNOWN = "unknown"                               # Needs manual classification


@dataclass
class IngestedItem:
    """A single piece of ingested data from any source."""
    source: str                    # sms, email, pdf
    content_type: ContentType
    raw_text: str
    amount: Optional[float] = None
    merchant: Optional[str] = None
    category: Optional[str] = None
    date: Optional[str] = None
    card_last4: Optional[str] = None
    bank: Optional[str] = None
    txn_type: Optional[str] = None  # debit, credit
    confidence: str = "low"
    metadata: dict = field(default_factory=dict)


class DataSource(ABC):
    """Abstract base class for input sources."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Unique identifier for this source (e.g., 'sms', 'email', 'pdf')."""
        pass

    @abstractmethod
    def sync(self, **kwargs) -> dict:
        """Pull new data from this source. Returns stats dict."""
        pass

    @abstractmethod
    def classify(self, raw_data: dict) -> ContentType:
        """Classify a raw data item into a content type."""
        pass

    @abstractmethod
    def process(self, raw_data: dict) -> List[IngestedItem]:
        """Parse raw data into IngestedItems."""
        pass

    def get_stats(self) -> dict:
        """Get source-specific stats. Override in subclasses."""
        return {}
