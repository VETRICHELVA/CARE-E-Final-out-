"""Relative dates for chat ordering (S17; apps-ai-iot.md: "by Friday", "in 72 hours" are
resolved in the user's time zone and shown back for confirmation).

The model only says what kind of date the user wrote (`When`); this module turns it into an
absolute time, deterministically, from a fixed `now` and the user's zone. The model never
does date arithmetic.

- "today", "tomorrow", "day after tomorrow": that local date.
- A weekday: its next occurrence on or after today ("by Friday" said on a Friday is today).
- A day of the month ("by 10th"), with or without a month and year: the next such date on
  or after today.
- "in 48 hours", "within 3 days": now plus that much, to the minute.
- Time of day: a clock time as given; morning 09:00, afternoon 14:00, evening 18:00,
  night 21:00, end of day 23:59; none given: 23:59 (the end of that day), flagged so the card
  says so.
"""

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
KINDS = ("today", "tomorrow", "day_after_tomorrow", "weekday", "date", "in_hours", "in_days")
TIME_OF_DAY = {
    "morning": time(9, 0),
    "afternoon": time(14, 0),
    "evening": time(18, 0),
    "night": time(21, 0),
    "end_of_day": time(23, 59),
}
END_OF_DAY = time(23, 59)


@dataclass(frozen=True)
class When:
    kind: str
    weekday: str | None = None
    day: int | None = None
    month: int | None = None
    year: int | None = None
    amount: float | None = None
    time_of_day: str | None = None
    clock_time: str | None = None  # "HH:MM", 24-hour


@dataclass(frozen=True)
class Resolved:
    at: datetime  # aware, in the user's zone
    time_given: bool  # False: no time of day was said, END_OF_DAY was used


def parse_clock(text: str | None) -> time | None:
    if not text:
        return None
    hh, sep, mm = text.strip().partition(":")
    if not sep or not hh.isdigit() or not mm.isdigit():
        return None
    h, m = int(hh), int(mm)
    return time(h, m) if 0 <= h <= 23 and 0 <= m <= 59 else None


def _next_day_of_month(today: date, day: int) -> date | None:
    year, month = today.year, today.month
    for _ in range(13):
        if day <= calendar.monthrange(year, month)[1] and date(year, month, day) >= today:
            return date(year, month, day)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return None


def _on_date(when: When, today: date) -> date | None:
    match when.kind:
        case "today":
            return today
        case "tomorrow":
            return today + timedelta(days=1)
        case "day_after_tomorrow":
            return today + timedelta(days=2)
        case "weekday":
            if when.weekday not in WEEKDAYS:
                return None
            return today + timedelta(days=(WEEKDAYS.index(when.weekday) - today.weekday()) % 7)
        case "date":
            if when.day is None or not 1 <= when.day <= 31:
                return None
            if when.month is None:
                return _next_day_of_month(today, when.day)
            if not 1 <= when.month <= 12:
                return None
            year = when.year or today.year
            for y in (year,) if when.year else (year, year + 1):
                if when.day <= calendar.monthrange(y, when.month)[1]:
                    found = date(y, when.month, when.day)
                    if when.year or found >= today:
                        return found
            return None
    return None


def resolve(when: When, now: datetime, tz: ZoneInfo) -> Resolved | None:
    """The absolute time `when` means for a user in `tz` at `now` (aware); None if it names
    no date (or an impossible one, such as 31 February)."""
    if when.kind in ("in_hours", "in_days"):
        if when.amount is None or when.amount <= 0:
            return None
        delta = (
            timedelta(hours=when.amount) if when.kind == "in_hours" else timedelta(days=when.amount)
        )
        return Resolved((now + delta).astimezone(tz), True)
    on = _on_date(when, now.astimezone(tz).date())
    if on is None:
        return None
    clock = parse_clock(when.clock_time) or TIME_OF_DAY.get(when.time_of_day or "")
    return Resolved(datetime.combine(on, clock or END_OF_DAY, tzinfo=tz), clock is not None)


def display(at: datetime, tz_name: str) -> str:
    """The required-by time in full, for the card: "Friday 9 October 2026, 23:59 (Asia/Kolkata)"."""
    return f"{at:%A} {at.day} {at:%B %Y, %H:%M} ({tz_name})"
