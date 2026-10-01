from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from common.models import (
    SchoolClass, Staff, Subject, Period, Holiday, TimetableEntry,
)
from common.holidays import (
    expand_holiday_range,
    group_consecutive_holidays,
    list_holidays as list_holiday_rows,
)
from common.exceptions import AppError, ConflictError, NotFoundError


def list_classes(db: Session, school_id: int) -> list[SchoolClass]:
    return db.query(SchoolClass).filter(SchoolClass.school_id == school_id, SchoolClass.is_active.is_(True)).order_by(SchoolClass.name).all()


def get_class(db: Session, school_id: int, class_id: int) -> SchoolClass | None:
    return db.query(SchoolClass).filter(
        SchoolClass.school_id == school_id, SchoolClass.class_id == class_id, SchoolClass.is_active.is_(True)
    ).first()


def create_class(db: Session, school_id: int, data: dict) -> SchoolClass:
    validate_class_teacher(db, school_id, data.get("class_teacher_staff_id"))
    cls = SchoolClass(school_id=school_id, **data)
    db.add(cls)
    db.commit()
    db.refresh(cls)
    return cls


def update_class(db: Session, cls: SchoolClass, data: dict) -> SchoolClass:
    if "class_teacher_staff_id" in data:
        validate_class_teacher(db, cls.school_id, data["class_teacher_staff_id"], cls.class_id)
    for k, v in data.items():
        if v is not None or k == "class_teacher_staff_id":
            setattr(cls, k, v)
    db.commit()
    db.refresh(cls)
    return cls


def validate_class_teacher(
    db: Session,
    school_id: int,
    teacher_id: int | None,
    class_id: int | None = None,
) -> None:
    if teacher_id is None:
        return

    teacher = db.query(Staff).filter(
        Staff.staff_id == teacher_id,
        Staff.school_id == school_id,
        Staff.person_type == "teacher",
        Staff.is_active.is_(True),
    ).first()
    if not teacher:
        raise NotFoundError("Teacher not found for this school")

    assigned_class = db.query(SchoolClass).filter(
        SchoolClass.school_id == school_id,
        SchoolClass.class_teacher_staff_id == teacher_id,
        SchoolClass.is_active.is_(True),
        SchoolClass.class_id != class_id if class_id is not None else True,
    ).first()
    if assigned_class:
        raise ConflictError(f"Teacher is already assigned to class '{assigned_class.name}'")


def delete_class(db: Session, cls: SchoolClass) -> None:
    cls.is_active = False
    db.commit()


def list_subjects(db: Session, school_id: int) -> list[Subject]:
    return db.query(Subject).filter(
        Subject.school_id == school_id
    ).order_by(Subject.is_active.desc(), Subject.name).all()


def get_subject(db: Session, school_id: int, subject_id: int) -> Subject | None:
    return db.query(Subject).filter(
        Subject.school_id == school_id, Subject.subject_id == subject_id
    ).first()


def subject_name_taken(db: Session, school_id: int, name: str, exclude_id: int | None = None) -> bool:
    query = db.query(Subject).filter(
        Subject.school_id == school_id,
        func.lower(Subject.name) == name.lower(),
    )
    if exclude_id is not None:
        query = query.filter(Subject.subject_id != exclude_id)
    return query.first() is not None


def create_subject(db: Session, school_id: int, name: str) -> Subject:
    if subject_name_taken(db, school_id, name):
        raise ConflictError("A subject with this name already exists for this school")
    subject = Subject(school_id=school_id, name=name)
    db.add(subject)
    db.commit()
    db.refresh(subject)
    return subject


def update_subject(db: Session, subject: Subject, data: dict) -> Subject:
    name = data.get("name")
    if name and subject_name_taken(db, subject.school_id, name, exclude_id=subject.subject_id):
        raise ConflictError("A subject with this name already exists for this school")
    subject.name = name
    db.commit()
    db.refresh(subject)
    return subject


def deactivate_subject(db: Session, subject: Subject) -> Subject:
    """Soft delete: marks, timetable entries, and assignments referencing this
    subject stay intact, and the subject can be reactivated later."""
    subject.is_active = False
    db.commit()
    db.refresh(subject)
    return subject


def activate_subject(db: Session, subject: Subject) -> Subject:
    if subject_name_taken(db, subject.school_id, subject.name, exclude_id=subject.subject_id):
        raise ConflictError("A subject with this name already exists for this school")
    subject.is_active = True
    db.commit()
    db.refresh(subject)
    return subject


def list_periods(db: Session) -> list[Period]:
    return db.query(Period).order_by(Period.period_no).all()


def update_period(
    db: Session,
    period_id: int,
    school_id: int,
    period_time: str | None,
    period_start_time,
    period_end_time,
) -> Period | None:
    period = db.query(Period).filter(Period.period_id == period_id).first()
    if not period:
        return None

    entries = db.query(TimetableEntry).filter(
        TimetableEntry.period_id == period_id,
        TimetableEntry.school_id == school_id,
    ).all()
    if not entries:
        return None

    if period_time is not None:
        period.period_time = period_time
    for entry in entries:
        if period_start_time is not None:
            entry.period_start_time = period_start_time
        if period_end_time is not None:
            entry.period_end_time = period_end_time

    db.commit()
    db.refresh(period)
    first_entry = entries[0]
    period.period_start_time = first_entry.period_start_time
    period.period_end_time = first_entry.period_end_time
    return period


def list_holidays(db: Session, school_id: int) -> list[Holiday]:
    """The school's holidays, soonest first. A pure read: an empty list means
    the school genuinely has no holidays recorded."""
    return list_holiday_rows(db, school_id)


def get_holiday(db: Session, school_id: int, holiday_id: int) -> Holiday | None:
    return db.query(Holiday).filter(
        Holiday.school_id == school_id, Holiday.holiday_id == holiday_id
    ).first()


def holiday_date_taken(
    db: Session, school_id: int, holiday_date: date, exclude_id: int | None = None
) -> bool:
    """True when the school already has a holiday on ``holiday_date``.

    Guarded in the repository rather than left to the database so the admin gets
    a readable 409 instead of a raw IntegrityError, and so updating a holiday
    without touching its date does not collide with itself.
    """
    query = db.query(Holiday).filter(
        Holiday.school_id == school_id, Holiday.holiday_date == holiday_date
    )
    if exclude_id is not None:
        query = query.filter(Holiday.holiday_id != exclude_id)
    return query.first() is not None


def taken_dates_in_range(
    db: Session, school_id: int, days: list[date], exclude_ids: set[int] | None = None
) -> list[date]:
    """Which of ``days`` the school already has a holiday on, in order.

    Takes the whole candidate list rather than one date so an overlapping range
    can be refused as a unit instead of half-applied.
    """
    if not days:
        return []
    query = db.query(Holiday.holiday_date).filter(
        Holiday.school_id == school_id, Holiday.holiday_date.in_(days)
    )
    if exclude_ids:
        query = query.filter(Holiday.holiday_id.notin_(exclude_ids))
    taken = {row for row, in query.all()}
    return [day for day in days if day in taken]


def _describe_taken(days: list[date]) -> str:
    """Name the conflicting days so the admin knows which part to change.

    Long ranges are common for a term break, and "this school already has a
    holiday on that date" would send the admin hunting for which one.
    """
    shown = ", ".join(day.isoformat() for day in days[:5])
    if len(days) > 5:
        shown += f" and {len(days) - 5} more"
    return f"These dates already have a holiday: {shown}"


def create_holiday(
    db: Session, school_id: int, occasion: str, holiday_date: date
) -> Holiday:
    return create_holiday_range(db, school_id, occasion, holiday_date, holiday_date)[0]


def create_holiday_range(
    db: Session,
    school_id: int,
    occasion: str,
    holiday_date: date,
    end_date: date | None = None,
) -> list[Holiday]:
    """Record ``occasion`` on every day from ``holiday_date`` to ``end_date``.

    Stored one row per day because that is what the timetable resolves against,
    so a multi-day break needs no timetable change to take effect.

    All or nothing: if any day in the span is already a holiday the whole request
    is refused. Writing the free days around a taken one would leave the school
    open on a day the admin believed was shut, which is the worse failure.
    """
    try:
        days = expand_holiday_range(holiday_date, end_date)
    except ValueError as error:
        raise AppError(str(error), status_code=400) from error
    taken = taken_dates_in_range(db, school_id, days)
    if taken:
        raise ConflictError(_describe_taken(taken))

    holidays = [
        Holiday(school_id=school_id, occasion=occasion, holiday_date=day) for day in days
    ]
    db.add_all(holidays)
    db.commit()
    for holiday in holidays:
        db.refresh(holiday)
    return holidays


def holiday_group(db: Session, holiday: Holiday) -> list[Holiday]:
    """The run of consecutive days sharing ``holiday``'s occasion, earliest first.

    This is the unit an admin means by "that holiday": one break that happens to
    occupy several rows. A gap ends the run, so a break added later next to an
    existing one stays separate instead of silently swallowing a school day.

    The run is found by grouping the school's matching rows rather than by
    windowing around the date, because a break can be any length and a fixed
    window would stop one day short of its end.
    """
    candidates = (
        db.query(Holiday)
        .filter(
            Holiday.school_id == holiday.school_id,
            Holiday.occasion == holiday.occasion,
        )
        .order_by(Holiday.holiday_date)
        .all()
    )
    for group in group_consecutive_holidays(candidates):
        if any(row.holiday_id == holiday.holiday_id for row in group):
            return group
    return [holiday]


def update_holiday(
    db: Session, holiday: Holiday, occasion: str | None, holiday_date: date | None
) -> Holiday:
    """Apply a partial update to a single-day holiday.

    Kept for single rows and existing callers; the portal edits a whole run via
    ``update_holiday_range``.
    """
    if holiday_date is not None and holiday_date != holiday.holiday_date:
        if holiday_date_taken(
            db, holiday.school_id, holiday_date, exclude_id=holiday.holiday_id
        ):
            raise ConflictError(
                f"This school already has a holiday on {holiday_date.isoformat()}"
            )
        holiday.holiday_date = holiday_date
    if occasion is not None:
        holiday.occasion = occasion
    db.commit()
    db.refresh(holiday)
    return holiday


def update_holiday_range(
    db: Session,
    holiday: Holiday,
    occasion: str | None,
    holiday_date: date | None,
    end_date: date | None = None,
) -> list[Holiday]:
    """Rename and/or reschedule a whole break, returning its new rows.

    The run is deleted and rewritten as the requested span rather than shifted in
    place. Moving a break by hand, day by day, would let it overlap itself partway
    through and needs a case per interior day; rewriting it keeps the span
    atomic, and the old rows go regardless of the conflict check below so a
    refusal never leaves the break half-shifted.
    """
    group = holiday_group(db, holiday)
    school_id = holiday.school_id
    current_first = group[0].holiday_date
    current_last = group[-1].holiday_date

    first = holiday_date if holiday_date is not None else current_first
    if end_date is not None:
        last = end_date
    elif holiday_date is not None:
        # A move that does not restate the span keeps its length, so shifting a
        # break by a day does not silently collapse it to a single day.
        last = first + timedelta(days=(current_last - current_first).days)
    else:
        last = current_last

    try:
        days = expand_holiday_range(first, last)
    except ValueError as error:
        raise AppError(str(error), status_code=400) from error

    exclude_ids = {row.holiday_id for row in group}
    taken = taken_dates_in_range(db, school_id, days, exclude_ids=exclude_ids)
    if taken:
        raise ConflictError(_describe_taken(taken))

    for row in group:
        db.delete(row)
    db.flush()
    name = occasion if occasion is not None else holiday.occasion
    replacement = [
        Holiday(school_id=school_id, occasion=name, holiday_date=day) for day in days
    ]
    db.add_all(replacement)
    db.commit()
    for row in replacement:
        db.refresh(row)
    return replacement


def delete_holiday(db: Session, holiday: Holiday) -> None:
    """Remove the holiday outright.

    A hard delete rather than the soft-delete pattern used for subjects: a
    holiday is a calendar fact, not a configurable entity that anything else
    references, so "removing" it should actually remove it.
    """
    db.delete(holiday)
    db.commit()


def delete_holiday_group(db: Session, holiday: Holiday) -> int:
    """Remove every day of the break ``holiday`` belongs to; return the count.

    Removing one row of a five-day break would leave four behind and read as a
    bug, so "remove" means the whole run.
    """
    group = holiday_group(db, holiday)
    for row in group:
        db.delete(row)
    db.commit()
    return len(group)
    db.commit()

