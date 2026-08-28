from sqlalchemy.orm import Session

from common.models import TimetableEntry, Holiday


def get_class_timetable(db: Session, class_id: int) -> list[TimetableEntry]:
    return db.query(TimetableEntry).filter(TimetableEntry.class_id == class_id).all()


def get_entry(db: Session, entry_id: int) -> TimetableEntry | None:
    return db.query(TimetableEntry).filter(TimetableEntry.entry_id == entry_id).first()


def update_entry(db: Session, entry: TimetableEntry, subject_id: int | None, teacher_id: int | None, school_id: int) -> TimetableEntry:
    if subject_id is not None:
        entry.subject_id = subject_id
    if teacher_id is not None:
        entry.teacher_id = teacher_id
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
