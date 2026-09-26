from datetime import datetime, time

from sqlalchemy.orm import Session

from common.models import TimetableEntry, Holiday, Period, SchoolClass, Subject
from common.exceptions import NotFoundError


def subject_in_school(db: Session, school_id: int, subject_id: int) -> bool:
    return db.query(Subject.subject_id).filter(
        Subject.subject_id == subject_id, Subject.school_id == school_id
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


def get_class_timetable(db: Session, class_id: int) -> list[TimetableEntry]:
    return db.query(TimetableEntry).filter(TimetableEntry.class_id == class_id).all()


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
            teacher_id=teacher_id,
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
) -> TimetableEntry:
    if subject_id is not None:
        if not subject_in_school(db, school_id, subject_id):
            raise NotFoundError("Subject not found for this school")
        entry.subject_id = subject_id
    if teacher_id is not None:
        entry.teacher_id = teacher_id
    if period_start_time is not None:
        entry.period_start_time = period_start_time
    if period_end_time is not None:
        entry.period_end_time = period_end_time
    # If this day is a school holiday, editing a period marks it as an
    # explicit override (an "extra class" scheduled despite the holiday).
    holiday = db.query(Holiday).filter(
        Holiday.school_id == school_id, Holiday.day_of_week == entry.day_of_week
    ).first()
    if holiday and holiday.is_holiday:
        entry.is_holiday_override = True
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
