"""
Time handling for Ingest (iter-18 HP4 / M8).

Storage stays in UTC (phone_timestamp and received_at are naive UTC ISO strings, exactly as
before, so SMS-row dedup across the realtime and backup paths keeps working). Every *calendar*
decision — which day a transaction belongs to, which billing cycle, "is it overdue yet" — is made
in India Standard Time, because that is the day the bank printed and the day the owner lived.

The containers run in UTC and must not depend on TZ being set: everything here is explicit.
"""
from datetime import datetime, date, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def now_ist() -> datetime:
    """Current wall-clock time in IST (timezone-aware)."""
    return datetime.now(timezone.utc).astimezone(IST)


def today_ist() -> date:
    return now_ist().date()


def utc_naive_iso_from_epoch(ts) -> str:
    """Epoch seconds or milliseconds -> naive UTC ISO string (the stored phone_timestamp format).

    Explicit UTC, so the result no longer depends on the container's TZ setting."""
    ts = float(ts)
    if ts > 1e12:
        ts = ts / 1000.0
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None).isoformat()


def parse_stored_timestamp(value):
    """Parse a stored timestamp into an aware UTC datetime, or None.

    Accepts: naive ISO ('2026-10-19T14:15:07.095000', assumed UTC), SQLite datetime('now')
    ('2026-10-19 14:15:08', UTC), ISO with an offset, and epoch seconds/milliseconds."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():
        try:
            return datetime.fromisoformat(utc_naive_iso_from_epoch(int(s))).replace(tzinfo=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def ist_date_of(value) -> str | None:
    """The IST calendar date (YYYY-MM-DD) of a stored UTC timestamp, or None."""
    dt = parse_stored_timestamp(value)
    if dt is None:
        return None
    return dt.astimezone(IST).date().isoformat()


def infer_year(month: int, day: int, reference: date, max_future_days: int = 7) -> int:
    """Year for a date printed without one (e.g. '31DEC', '13OCT', '18-10').

    Banks only print year-less dates for recent events, so pick the year that puts the date
    at or before the reference date (allowing a small grace for clock skew). An SMS saying
    '31DEC' processed on 2 January belongs to the previous year, not eleven months ahead."""
    year = reference.year
    try:
        candidate = date(year, month, day)
    except ValueError:
        # 29 Feb in a non-leap reference year: walk back to the nearest leap year.
        for back in range(1, 4):
            try:
                return date(year - back, month, day).year
            except ValueError:
                continue
        return year
    if candidate > reference + timedelta(days=max_future_days):
        return year - 1
    return year
