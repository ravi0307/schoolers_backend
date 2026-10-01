from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from common.models import TimetableEntry, Period, SchoolClass, Staff, Subject
from common.exceptions import NotFoundError
from common.holidays import holidays_in_range
from common.week import monday_of, week_dates


def subject_in_school(db: Session, school_id: int, subject_id: int) -> bool:
    return db.query(Subject.subject_id).filter(
        Subject.subject_id == subject_id, Subject.school_id == school_id
    ).first() is not None


def teacher_in_school(db: Session, school_id: int, teacher_id: int) -> bool:
    """True when the staff id is an active teacher inside this school."""
    return db.query(Staff.staff_id).filter(
        Staff.staff_id == teacher_id,
        Staff.school_id == school_id,
        Staff.person_type == "teacher",
        Staff.is_active.is_(True),
    ).first() is not None


def _parse_period_times(period_time: str) -> tuple[time | None, time | None]:
    parts = [part.strip() for part in period_time.split("-", 1)]
    if len(parts) != 2:
        return None, None
    try:
        return tuple(
            datetime.strptime(part, "%I:%M %p").time()
            for part in parts
        )
    except ValueError:
        return None, None


def get_class_in_school(db: Session, class_id: int, school_id: int) -> SchoolClass | None:
    """The class, but only if it belongs to the caller's school."""
    return db.query(SchoolClass).filter(
        SchoolClass.class_id == class_id,
        SchoolClass.school_id == school_id,
        SchoolClass.is_active.is_(True),
    ).first()


def get_class_timetable(db: Session, class_id: int, school_id: int | None = None) -> list[TimetableEntry]:
    query = db.query(TimetableEntry).filter(TimetableEntry.class_id == class_id)
    if school_id is not None:
        query = query.filter(TimetableEntry.school_id == school_id)
    return query.all()


def get_class_week(
    db: Session,
    class_id: int,
    school_id: int,
    week_start: date | None = None,
) -> dict:
    """Resolve a class's recurring timetable against one calendar week.

    ``week_start`` may be any day inside the week; it is normalised to that
    week's Monday. Holidays are named, dated rows, so a column is only red when
    one of the week's actual dates matches a holiday — a date outside the
    displayed week never highlights anything.
    """
    start = monday_of(week_start or date.today())
    entries = get_class_timetable(db, class_id, school_id)
    holidays = holidays_in_range(db, school_id, start, start + timedelta(days=6))
    days = []
    for abbreviation, day_date in week_dates(start):
        occasion = holidays.get(day_date)
        days.append(
            {
                "day_of_week": abbreviation,
                "date": day_date,
                "is_holiday": occasion is not None,
                "holiday_name": occasion,
                "entries": [e for e in entries if e.day_of_week == abbreviation],
            }
        )
    return {
        "class_id": class_id,
        "school_id": school_id,
        "week_start": start,
        "week_end": start + timedelta(days=6),
        "days": days,
    }


def get_entry(db: Session, entry_id: int, school_id: int | None = None) -> TimetableEntry | None:
    query = db.query(TimetableEntry).filter(TimetableEntry.entry_id == entry_id)
    if school_id is not None:
        query = query.filter(TimetableEntry.school_id == school_id)
    return query.first()


def create_week_period(
    db: Session,
    class_id: int,
    period_time: str,
    school_id: int,
    created_by: int | None = None,
    subject_id: int | None = None,
    teacher_id: int | None = None,
    day_of_week: str | None = None,
) -> list[TimetableEntry]:
    school_class = db.query(SchoolClass).filter(
        SchoolClass.class_id == class_id,
        SchoolClass.school_id == school_id,
        SchoolClass.is_active.is_(True),
    ).first()
    if not school_class:
        return []

    if subject_id is not None and not subject_in_school(db, school_id, subject_id):
        raise NotFoundError("Subject not found for this school")

    # Never trust a teacher id from the body: it must be an active teacher in
    # this same school. (Previously unvalidated, which allowed cross-school ids.)
    if teacher_id is not None and not teacher_in_school(db, school_id, teacher_id):
        raise NotFoundError("Teacher not found for this school")

    last_period = db.query(Period).order_by(Period.period_no.desc()).first()
    period = Period(
        period_no=(last_period.period_no + 1 if last_period else 1),
        period_time=period_time,
    )
    db.add(period)
    db.flush()

    period_start_time, period_end_time = _parse_period_times(period_time)
    days = (day_of_week,) if day_of_week else ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    entries = [
        TimetableEntry(
            school_id=school_id,
            class_id=class_id,
            day_of_week=day,
            period_id=period.period_id,
            subject_id=subject_id,
            staff_id=teacher_id,
            period_start_time=period_start_time,
            period_end_time=period_end_time,
            created_by=created_by,
        )
        for day in days
    ]
    db.add_all(entries)
    db.commit()
    for entry in entries:
        db.refresh(entry)
    return entries


def update_entry(
    db: Session,
    entry: TimetableEntry,
    subject_id: int | None,
    teacher_id: int | None,
    period_start_time,
    period_end_time,
    school_id: int,
    is_holiday_override: bool | None = None,
) -> TimetableEntry:
    if subject_id is not None:
        if not subject_in_school(db, school_id, subject_id):
            raise NotFoundError("Subject not found for this school")
        entry.subject_id = subject_id
    if teacher_id is not None:
        if not teacher_in_school(db, school_id, teacher_id):
            raise NotFoundError("Teacher not found for this school")
        entry.staff_id = teacher_id
    if period_start_time is not None:
        entry.period_start_time = period_start_time
    if period_end_time is not None:
        entry.period_end_time = period_end_time
    # The override flag is set explicitly by the caller, not derived here.
    # It used to be inferred from "is this weekday a recurring holiday?", but
    # holidays are now specific dates while an entry is a weekday template with
    # no date, so there is nothing to compare and any guess would be wrong.
    if is_holiday_override is not None:
        entry.is_holiday_override = is_holiday_override
    db.commit()
    db.refresh(entry)
    return entry


def clear_override(db: Session, entry: TimetableEntry) -> TimetableEntry:
    entry.is_holiday_override = False
    db.commit()
    db.refresh(entry)
    return entry


def delete_entry(db: Session, entry: TimetableEntry) -> None:
    """Hard-delete a timetable entry. Returns the entry id for confirmation."""
    db.delete(entry)
    db.commit()
