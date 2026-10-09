"""Relative dates for chat ordering (S17; apps-ai-iot.md: "by Friday", "in 72 hours" are
resolved in the user's time zone and shown back for confirmation).

The model only says what kind of date the user wrote (`When`); this module turns it into an
absolute time, deterministically, from a fixed `now` and the user's zone. The model never
does date arithmetic.

- "today", "tomorrow", "day after tomorrow": that local date.
- A weekday, alone or as "this <weekday>": its next occurrence on or after today ("by
  Friday" said on a Friday is today). "next <weekday>": that weekday in the following week
  (weeks start on Monday), so "next Friday" said on a Monday or a Friday is the Friday after
  this week's.
- A day of the month alone ("by 10th"): the next such date on or after today.
- A day and month without a year ("by 5 Oct"): that date this year, even if it has passed
  (the card then says it has; the year is never guessed forward). With a year: that date.
- "in 48 hours", "within 3 days": now plus that much, to the minute.
- Time of day: a clock time as given; "end of day" (EOD) 23:59. A word with no clock time is
  an assumed hour, flagged so the card says so: morning 09:00, afternoon 14:00, evening 18:00,
  night 21:00. None given: 23:59 (the end of that day), flagged the same way.
"""

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
KINDS = ("today", "tomorrow", "day_after_tomorrow", "weekday", "date", "in_hours", "in_days")
MODIFIERS = ("this", "next")  # "this friday" = "friday"; "next friday" = the following week's
END_OF_DAY = time(23, 59)
# Hours assumed for a time-of-day word: the user gave no figure, so each is flagged.
ASSUMED_HOURS = {
    "morning": time(9, 0),
    "afternoon": time(14, 0),
    "evening": time(18, 0),
    "night": time(21, 0),
}
TIME_OF_DAY = (*ASSUMED_HOURS, "end_of_day")


@dataclass(frozen=True)
class When:
    kind: str
    weekday: str | None = None
    modifier: str | None = None  # for a weekday: "this", "next" or None
    day: int | None = None
    month: int | None = None
    year: int | None = None
    amount: float | None = None
    time_of_day: str | None = None
    clock_time: str | None = None  # "HH:MM", 24-hour


@dataclass(frozen=True)
class Resolved:
    at: datetime  # aware, in the user's zone
    time_given: bool  # False: no clock time was said; an assumed hour or END_OF_DAY was used
    assumed: str | None = None  # the time-of-day word whose hour was assumed, e.g. "morning"


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
            if when.weekday not in WEEKDAYS or when.modifier not in (None, *MODIFIERS):
                return None
            target = WEEKDAYS.index(when.weekday)
            if when.modifier == "next":  # the following week's (weeks start on Monday)
                return today + timedelta(days=7 - today.weekday() + target)
            return today + timedelta(days=(target - today.weekday()) % 7)
        case "date":
            if when.day is None or not 1 <= when.day <= 31:
                return None
            if when.month is None:
                return _next_day_of_month(today, when.day)
            if not 1 <= when.month <= 12:
                return None
            # No year: this year's, never next year's, so a passed date is shown as passed.
            year = when.year or today.year
            if when.day > calendar.monthrange(year, when.month)[1]:
                return None
            return date(year, when.month, when.day)
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
    clock = parse_clock(when.clock_time)
    assumed = ASSUMED_HOURS.get(when.time_of_day or "")
    if clock is not None:
        return Resolved(datetime.combine(on, clock, tzinfo=tz), True)
    if when.time_of_day == "end_of_day":
        return Resolved(datetime.combine(on, END_OF_DAY, tzinfo=tz), True)
    if assumed is not None:
        return Resolved(datetime.combine(on, assumed, tzinfo=tz), False, when.time_of_day)
    return Resolved(datetime.combine(on, END_OF_DAY, tzinfo=tz), False)


def display(at: datetime, tz_name: str) -> str:
    """The required-by time in full, for the card: "Friday 9 October 2026, 23:59 (Asia/Kolkata)"."""
    return f"{at:%A} {at.day} {at:%B %Y, %H:%M} ({tz_name})"
