"""Parse BTC Up/Down market question text to extract window times."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(AM|PM)")
_DATE_RE = re.compile(r"-\s+(\w+)\s+(\d{1,2}),")
_UP_DOWN_RE = re.compile(r"bitcoin\s+up\s+or\s+down", re.IGNORECASE)

MONTHS: dict[str, int] = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}


def _to_24h(h: int, m: int, ampm: str) -> tuple[int, int]:
    hour = (0 if h == 12 else h) if ampm == "AM" else (h if h == 12 else h + 12)
    return hour, m


@dataclass(frozen=True)
class WindowInfo:
    """Parsed window metadata from a market question."""

    condition_id: str
    window_start_utc: datetime
    window_end_utc: datetime
    duration_min: int
    token_yes: str  # asset_id for UP token
    token_no: str   # asset_id for DOWN token

    @property
    def is_active(self) -> bool:
        now = datetime.now(UTC)
        return self.window_start_utc <= now < self.window_end_utc

    @property
    def minutes_elapsed(self) -> float:
        now = datetime.now(UTC)
        return (now - self.window_start_utc).total_seconds() / 60

    @property
    def minutes_remaining(self) -> float:
        now = datetime.now(UTC)
        return (self.window_end_utc - now).total_seconds() / 60


def is_btc_up_down(question: str) -> bool:
    """Check if a market question is a BTC Up or Down market with time range."""
    return bool(_UP_DOWN_RE.search(question)) and bool(_TIME_RE.search(question))


def _infer_year(month: int) -> int:
    """Infer year from month — data spans Sep 2025 to present."""
    now = datetime.now(UTC)
    # If the month is in the future by more than 1 month, assume last year
    candidate = datetime(now.year, month, 1, tzinfo=UTC)
    if candidate > now + timedelta(days=45):
        return now.year - 1
    # If the month is far in the past, might be current year
    return now.year


def parse_window(
    question: str,
    condition_id: str = "",
    token_yes: str = "",
    token_no: str = "",
) -> WindowInfo | None:
    """Parse question to extract window start/end UTC.

    Example: "Bitcoin Up or Down - December 1, 10:00AM-10:15AM ET"
    """
    date_match = _DATE_RE.search(question)
    if not date_match:
        return None

    month_name = date_match.group(1)
    day = int(date_match.group(2))
    month = MONTHS.get(month_name)
    if month is None:
        return None

    year = _infer_year(month)

    times = _TIME_RE.findall(question)
    if len(times) < 2:
        return None

    h1, m1, ap1 = int(times[0][0]), int(times[0][1]), times[0][2]
    h2, m2, ap2 = int(times[1][0]), int(times[1][1]), times[1][2]

    hour1, min1 = _to_24h(h1, m1, ap1)
    hour2, min2 = _to_24h(h2, m2, ap2)

    try:
        start_et = datetime(year, month, day, hour1, min1, tzinfo=ET)
        end_et = datetime(year, month, day, hour2, min2, tzinfo=ET)

        # Handle midnight crossing (e.g. 11:55PM-12:00AM)
        if end_et <= start_et:
            end_et += timedelta(days=1)

        start_utc = start_et.astimezone(UTC)
        end_utc = end_et.astimezone(UTC)
        duration_min = int((end_utc - start_utc).total_seconds() / 60)

        if duration_min not in (5, 15):
            return None

        return WindowInfo(
            condition_id=condition_id,
            window_start_utc=start_utc,
            window_end_utc=end_utc,
            duration_min=duration_min,
            token_yes=token_yes,
            token_no=token_no,
        )
    except (ValueError, OverflowError):
        return None
