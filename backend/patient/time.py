from datetime import datetime, timezone


def utc_naive(value: datetime) -> datetime:
    """Normalize API datetimes for the project's timezone-naive UTC database columns."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
