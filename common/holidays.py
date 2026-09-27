"""
Holiday lookups shared by the academics and timetable services.

``holidays`` holds one row per named holiday on a specific calendar date
(``occasion`` + ``holiday_date``), not a recurring weekday flag. Two services
read it — academics (the admin portal's list) and timetable (which decides which
columns of a displayed week are red) — and services cannot import each other, so
the queries live here against the shared model.

The date range lookup is the important one: the timetable is stored as a
recurring weekday template, so a holiday only becomes visible once a specific
week's real dates are known, and that resolution happens in one place to keep
the API and the browser from disagreeing about which column to highlight.
"""
from datetime import date

from sqlalchemy.orm import Session

from common.models import Holiday


def list_holidays(db: Session, school_id: int) -> list[Holiday]:
    """Every holiday for a school, soonest first.

    A pure read: unlike the old recurring template there are no placeholder rows
    to backfill, so an empty result genuinely means the school has no holidays.
    """
    return (
        db.query(Holiday)
        .filter(Holiday.school_id == school_id)
        .order_by(Holiday.holiday_date, Holiday.occasion)
        .all()
    )


def holidays_in_range(
    db: Session, school_id: int, start: date, end: date
) -> dict[date, str]:
    """``{date: occasion}`` for holidays falling in the inclusive range.

    Dates absent from the result are ordinary school days. The unique constraint
    on ``(school_id, holiday_date)`` means at most one occasion per date, so
    this mapping is unambiguous.
    """
    rows = (
        db.query(Holiday)
        .filter(
            Holiday.school_id == school_id,
            Holiday.holiday_date >= start,
            Holiday.holiday_date <= end,
        )
        .all()
    )
    return {row.holiday_date: row.occasion for row in rows}
