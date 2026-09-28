from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def to_et(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError(f"naive datetime {ts!r}: all timestamps must be timezone-aware")
    return ts.astimezone(ET)
