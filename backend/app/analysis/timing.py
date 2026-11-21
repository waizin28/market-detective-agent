"""Time-aware evidence classification (Week 3 failure mode, Week 4/6 regression test).

A daily move on trading day T is measured close(T-1) -> close(T) (16:00 ET).
Information can only be the *public trigger* if it was public by 16:00 ET on T.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.models import Timing

ET = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)


def market_close(d: date) -> datetime:
    return datetime.combine(d, MARKET_CLOSE, tzinfo=ET)


def classify(
    published_at: datetime | None,
    *,
    event_date: date,
    prev_date: date,
    date_only: bool = False,
) -> Timing:
    """Return when the evidence became public relative to the move.

    - ``before``: public before the move window opened (at/before prior close)
    - ``during``: public inside the move window (prior close, event-day close]
    - ``after``:  public after the event-day close -> can never be the trigger
    - ``unknown``: no usable timestamp -> recorded as an evidence gap
    """
    if published_at is None:
        return "unknown"
    if date_only:
        d = published_at.date()
        if d < event_date:
            # Strictly before the event day. If it's on the prior trading day
            # we can't tell before vs after that close, but either way it was
            # public before the event-day close.
            return "before" if d < prev_date else "during"
        if d > event_date:
            return "after"
        return "unknown"  # same day, time unknown: can't place relative to 16:00
    if published_at.tzinfo is None:
        raise ValueError("published_at must be timezone-aware")
    if published_at <= market_close(prev_date):
        return "before"
    if published_at <= market_close(event_date):
        return "during"
    return "after"


def can_be_trigger(timing: Timing) -> bool:
    return timing in ("before", "during")
