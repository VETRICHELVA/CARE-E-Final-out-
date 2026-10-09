"""Relative dates resolve in the user's zone from a fixed `now` (S17 acceptance criterion 4)."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from app.dates import When, display, resolve

IST = ZoneInfo("Asia/Kolkata")
UTC_TZ = ZoneInfo("UTC")
MONDAY = datetime(2026, 10, 5, 4, 30, tzinfo=UTC)  # Mon 10:00 IST, Mon 04:30 UTC
THU_NIGHT_UTC = datetime(2026, 10, 8, 20, 0, tzinfo=UTC)  # Thu 20:00 UTC = Fri 01:30 IST
FRI_NIGHT_UTC = datetime(2026, 10, 9, 20, 0, tzinfo=UTC)  # Fri 20:00 UTC = Sat 01:30 IST
FRIDAY = When("weekday", weekday="friday")


def at(when: When, now: datetime, tz: ZoneInfo) -> str:
    resolved = resolve(when, now, tz)
    assert resolved is not None
    return resolved.at.isoformat()


def test_by_friday_from_monday_is_this_friday_in_both_zones() -> None:
    assert at(FRIDAY, MONDAY, IST) == "2026-10-09T23:59:00+05:30"
    assert at(FRIDAY, MONDAY, UTC_TZ) == "2026-10-09T23:59:00+00:00"


def test_by_friday_when_it_is_already_friday_in_kolkata() -> None:
    # Thursday evening in UTC is already Friday in Kolkata: both mean Friday 9 October,
    # but each user's own end of day.
    assert at(FRIDAY, THU_NIGHT_UTC, IST) == "2026-10-09T23:59:00+05:30"
    assert at(FRIDAY, THU_NIGHT_UTC, UTC_TZ) == "2026-10-09T23:59:00+00:00"


def test_by_friday_differs_by_a_week_once_kolkata_has_passed_friday() -> None:
    # Friday 20:00 UTC is Saturday 01:30 in Kolkata: the next Friday there is 16 October.
    assert at(FRIDAY, FRI_NIGHT_UTC, UTC_TZ) == "2026-10-09T23:59:00+00:00"
    assert at(FRIDAY, FRI_NIGHT_UTC, IST) == "2026-10-16T23:59:00+05:30"


def test_tomorrow_is_the_users_tomorrow() -> None:  # evals o12 / o13
    tomorrow = When("tomorrow")
    assert at(tomorrow, THU_NIGHT_UTC, IST).startswith("2026-10-10T")
    assert at(tomorrow, THU_NIGHT_UTC, UTC_TZ).startswith("2026-10-09T")


def test_hours_are_exact_and_times_of_day_are_local() -> None:
    assert at(When("in_hours", amount=72), MONDAY, IST) == "2026-10-08T10:00:00+05:30"
    assert at(When("in_days", amount=2), MONDAY, UTC_TZ) == "2026-10-07T04:30:00+00:00"
    six_pm = When("today", clock_time="18:00")
    assert at(six_pm, MONDAY, IST) == "2026-10-05T18:00:00+05:30"  # = 12:30 UTC (eval o05)
    morning = resolve(When("tomorrow", time_of_day="morning"), MONDAY, IST)
    assert morning is not None and morning.time_given
    assert morning.at.isoformat() == "2026-10-06T09:00:00+05:30"
    no_time = resolve(When("day_after_tomorrow"), MONDAY, IST)
    assert no_time is not None and not no_time.time_given
    assert no_time.at.isoformat() == "2026-10-07T23:59:00+05:30"


def test_days_of_the_month_roll_forward() -> None:
    assert at(When("date", day=10), MONDAY, IST).startswith("2026-10-10")
    assert at(When("date", day=3), MONDAY, IST).startswith("2026-11-03")  # 3rd has passed
    assert at(When("date", day=31), datetime(2026, 11, 5, tzinfo=UTC), IST).startswith(
        "2026-12-31"  # November has no 31st
    )
    assert at(When("date", day=12, month=10), MONDAY, IST).startswith("2026-10-12")
    assert at(When("date", day=1, month=1), MONDAY, IST).startswith("2027-01-01")
    assert at(When("date", day=1, month=1, year=2026), MONDAY, IST).startswith("2026-01-01")


@pytest.mark.parametrize(
    "when",
    [
        When("date", day=30, month=2),
        When("date", day=32),
        When("date"),
        When("weekday", weekday="someday"),
        When("in_hours"),
        When("in_hours", amount=0),
    ],
)
def test_impossible_or_incomplete_dates_resolve_to_nothing(when: When) -> None:
    assert resolve(when, MONDAY, IST) is None


def test_display_shows_the_date_in_full() -> None:
    resolved = resolve(FRIDAY, MONDAY, IST)
    assert resolved is not None
    assert display(resolved.at, "Asia/Kolkata") == "Friday 9 October 2026, 23:59 (Asia/Kolkata)"
