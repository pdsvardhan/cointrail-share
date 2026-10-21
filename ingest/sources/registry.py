"""
Source registry — central place to manage all data sources.
New sources (Telegram, Notifications, etc.) register here.
"""
from sources.base import DataSource, ContentType

_SOURCES = {}


def register_source(source: DataSource):
    """Register a data source."""
    _SOURCES[source.source_name] = source


def get_source(name: str) -> DataSource:
    """Get a registered source by name."""
    return _SOURCES.get(name)


def list_sources() -> dict:
    """List all registered sources with stats."""
    result = {}
    for name, source in _SOURCES.items():
        try:
            stats = source.get_stats()
        except Exception:
            stats = {}
        result[name] = {"name": name, "stats": stats}
    return result


def init_sources():
    """Initialize and register all available sources."""
    from sources.sms.source import SmsSource
    from sources.email.source import EmailSource
    from sources.pdf.source import PdfSource

    register_source(SmsSource())
    register_source(EmailSource())
    register_source(PdfSource())

    print(f"Registered {len(_SOURCES)} data sources: {list(_SOURCES.keys())}")
