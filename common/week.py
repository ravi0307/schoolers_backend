"""
Week arithmetic shared by the timetable surfaces.

Timetable entries are stored as a recurring weekday template (one row per
``day_of_week``), so a "week" is a real calendar week identified by its Monday.
Keeping the maths in one module stops the API and the browser from disagreeing
about which Monday a given date belongs to — a mismatch that would silently
highlight the wrong column as a holiday.
"""
from datetime import date, timedelta

#: Weekday abbreviations, Monday first. Matches ``timetable_entries.day_of_week``.
DAY_ABBREVIATIONS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def monday_of(value: date) -> date:
    """The Monday of the week containing ``value``."""
    return value - timedelta(days=value.weekday())


def week_dates(week_start: date) -> list[tuple[str, date]]:
    """``(day abbreviation, date)`` for all seven days of a week, Monday first.

    ``week_start`` may be any day inside the target week; it is normalised to
    that week's Monday, so callers can pass a raw date without pre-rounding.
    """
    monday = monday_of(week_start)
    return [(DAY_ABBREVIATIONS[i], monday + timedelta(days=i)) for i in range(7)]

