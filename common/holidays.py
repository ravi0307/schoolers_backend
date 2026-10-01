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
from datetime import date, timedelta

from sqlalchemy.orm import Session

from common.models import Holiday


def expand_holiday_range(start: date, end: date | None = None) -> list[date]:
    """Every calendar date from ``start`` to ``end`` inclusive.

    A range is a request, not a stored thing: ``holidays`` keeps one row per date
    so the timetable can resolve any day on its own. An absent ``end`` means one
    day, which is why a single-day selection and a range are one code path rather
    than two.

    Weekends are included. The admin picked a span of closed days and the school
    calendar is seven days wide, so "the days in between" is taken literally --
    silently dropping Saturdays would shorten a closure nobody asked to shorten.
    """
    last = end if end is not None else start
    if last < start:
        raise ValueError(
            f"The last day ({last.isoformat()}) is before the first day "
            f"({start.isoformat()})"
        )
    span = (last - start).days
    return [start + timedelta(days=offset) for offset in range(span + 1)]


def group_consecutive_holidays(rows: list[Holiday]) -> list[list[Holiday]]:
    """Split dated rows into runs of consecutive dates sharing one occasion.

    This is what makes a multi-day break look like the single thing an admin
    entered rather than five identical rows. A gap breaks the run: "Diwali" on
    the 8th and "Diwali" on the 10th are two breaks, and grouping them would
    invent a closure on the 9th that nobody asked for.

    ``rows`` must be ordered by date. Sorting here would hide an ordering bug
    from callers, so it is the caller's job, exactly as for ``list_holidays``.
    """
    groups: list[list[Holiday]] = []
    for row in rows:
        if groups:
            run = groups[-1]
            previous = run[-1]
            adjacent = row.holiday_date == previous.holiday_date + timedelta(days=1)
            if adjacent and row.occasion == previous.occasion:
                run.append(row)
                continue
        groups.append([row])
    return groups


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
