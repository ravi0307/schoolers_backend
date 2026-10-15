from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from common.models import (
    SchoolClass, Staff, Subject, Period, Holiday, TimetableEntry,
    AcademicYear, AcademicPeriod, AcademicEvent,
    ExaminationType, GradeScale, GradeLevel,
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



# ---- Academic calendar (Phase 1): years, periods, dated events ----


def list_years(db: Session, school_id: int) -> list[AcademicYear]:
    """The school's academic years, newest first."""
    return (
        db.query(AcademicYear)
        .filter(AcademicYear.school_id == school_id)
        .order_by(AcademicYear.start_date.desc())
        .all()
    )


def get_year(db: Session, school_id: int, year_id: int) -> AcademicYear | None:
    return (
        db.query(AcademicYear)
        .filter(AcademicYear.school_id == school_id, AcademicYear.year_id == year_id)
        .first()
    )


def year_name_taken(db: Session, school_id: int, name: str, exclude_id: int | None = None) -> bool:
    query = db.query(AcademicYear).filter(
        AcademicYear.school_id == school_id, AcademicYear.name == name
    )
    if exclude_id is not None:
        query = query.filter(AcademicYear.year_id != exclude_id)
    return query.first() is not None


def create_year(db: Session, school_id: int, data: dict) -> AcademicYear:
    if year_name_taken(db, school_id, data["name"]):
        raise ConflictError("An academic year with this name already exists for this school")
    year = AcademicYear(school_id=school_id, **data)
    db.add(year)
    db.commit()
    db.refresh(year)
    return year


def update_year(db: Session, year: AcademicYear, data: dict) -> AcademicYear:
    name = data.get("name")
    if name and year_name_taken(db, year.school_id, name, exclude_id=year.year_id):
        raise ConflictError("An academic year with this name already exists for this school")
    start = data.get("start_date", year.start_date)
    end = data.get("end_date", year.end_date)
    if end <= start:
        raise AppError("The year end date must be after the start date", status_code=400)
    for k, v in data.items():
        if v is not None:
            setattr(year, k, v)
    db.commit()
    db.refresh(year)
    return year


def list_periods(db: Session, year_id: int) -> list[AcademicPeriod]:
    return (
        db.query(AcademicPeriod)
        .filter(AcademicPeriod.year_id == year_id)
        .order_by(AcademicPeriod.start_date)
        .all()
    )


def get_period_in_school(db: Session, school_id: int, period_id: int) -> AcademicPeriod | None:
    """A period plus its year, both constrained to the caller's school."""
    return (
        db.query(AcademicPeriod)
        .join(AcademicYear, AcademicYear.year_id == AcademicPeriod.year_id)
        .filter(AcademicPeriod.period_id == period_id, AcademicYear.school_id == school_id)
        .first()
    )


def period_name_taken(
    db: Session, year_id: int, name: str, exclude_id: int | None = None
) -> bool:
    query = db.query(AcademicPeriod).filter(
        AcademicPeriod.year_id == year_id, AcademicPeriod.name == name
    )
    if exclude_id is not None:
        query = query.filter(AcademicPeriod.period_id != exclude_id)
    return query.first() is not None


def period_overlaps(
    db: Session,
    year_id: int,
    start_date,
    end_date,
    exclude_id: int | None = None,
) -> bool:
    """True when [start_date, end_date] intersects any sibling period."""
    query = db.query(AcademicPeriod).filter(
        AcademicPeriod.year_id == year_id,
        AcademicPeriod.start_date <= end_date,
        AcademicPeriod.end_date >= start_date,
    )
    if exclude_id is not None:
        query = query.filter(AcademicPeriod.period_id != exclude_id)
    return query.first() is not None


def create_period(db: Session, year: AcademicYear, data: dict) -> AcademicPeriod:
    if period_name_taken(db, year.year_id, data["name"]):
        raise ConflictError("A period with this name already exists for this academic year")
    if data["start_date"] < year.start_date or data["end_date"] > year.end_date:
        raise AppError("The period must fall within its academic year", status_code=400)
    if period_overlaps(db, year.year_id, data["start_date"], data["end_date"]):
        raise ConflictError("The period overlaps another period in this academic year")
    period = AcademicPeriod(year_id=year.year_id, **data)
    db.add(period)
    db.commit()
    db.refresh(period)
    return period


def update_period_entry(db: Session, period: AcademicPeriod, data: dict) -> AcademicPeriod:
    name = data.get("name")
    if name and period_name_taken(db, period.year_id, name, exclude_id=period.period_id):
        raise ConflictError("A period with this name already exists for this academic year")
    start = data.get("start_date", period.start_date)
    end = data.get("end_date", period.end_date)
    year = db.query(AcademicYear).filter(AcademicYear.year_id == period.year_id).first()
    if year is not None and (start < year.start_date or end > year.end_date):
        raise AppError("The period must fall within its academic year", status_code=400)
    if period_overlaps(db, period.year_id, start, end, exclude_id=period.period_id):
        raise ConflictError("The period overlaps another period in this academic year")
    for k, v in data.items():
        if v is not None:
            setattr(period, k, v)
    db.commit()
    db.refresh(period)
    return period


def list_events(
    db: Session, school_id: int, year_id: int | None = None
) -> list[AcademicEvent]:
    """Dated non-closure events, soonest first, optionally for one year."""
    query = db.query(AcademicEvent).filter(AcademicEvent.school_id == school_id)
    if year_id is not None:
        query = query.filter(AcademicEvent.year_id == year_id)
    return query.order_by(AcademicEvent.start_date).all()


def get_event(db: Session, school_id: int, event_id: int) -> AcademicEvent | None:
    return (
        db.query(AcademicEvent)
        .filter(AcademicEvent.school_id == school_id, AcademicEvent.event_id == event_id)
        .first()
    )


def create_event(db: Session, school_id: int, data: dict) -> AcademicEvent:
    year_id = data.get("year_id")
    if year_id is not None and get_year(db, school_id, year_id) is None:
        raise NotFoundError("Academic year not found")
    end = data.get("end_date") or data["start_date"]
    event = AcademicEvent(
        school_id=school_id,
        year_id=year_id,
        event_type=data["event_type"],
        title=data["title"],
        start_date=data["start_date"],
        end_date=end,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def update_event(db: Session, event: AcademicEvent, data: dict) -> AcademicEvent:
    if "year_id" in data and data["year_id"] is not None:
        if get_year(db, event.school_id, data["year_id"]) is None:
            raise NotFoundError("Academic year not found")
    for k, v in data.items():
        # year_id may be explicitly cleared (unlink from the year).
        if v is not None or k == "year_id":
            setattr(event, k, v)
    if event.end_date < event.start_date:
        raise AppError("The event end date cannot be before the start date", status_code=400)
    db.commit()
    db.refresh(event)
    return event


def delete_event(db: Session, event: AcademicEvent) -> None:
    db.delete(event)
    db.commit()


# ---- Examination setup (Phase 2): types, grade scales ----


def list_examination_types(db: Session, school_id: int):
    return (
        db.query(ExaminationType)
        .filter(ExaminationType.school_id == school_id)
        .order_by(ExaminationType.name)
        .all()
    )


def get_examination_type(db: Session, school_id: int, type_id: int):
    return (
        db.query(ExaminationType)
        .filter(ExaminationType.school_id == school_id, ExaminationType.type_id == type_id)
        .first()
    )


def type_name_taken(db: Session, school_id: int, name: str, exclude_id: int | None = None) -> bool:
    query = db.query(ExaminationType).filter(
        ExaminationType.school_id == school_id, ExaminationType.name == name
    )
    if exclude_id is not None:
        query = query.filter(ExaminationType.type_id != exclude_id)
    return query.first() is not None


def create_examination_type(db: Session, school_id: int, data: dict):
    if type_name_taken(db, school_id, data["name"]):
        raise ConflictError("An examination type with this name already exists for this school")
    exam_type = ExaminationType(school_id=school_id, **data)
    db.add(exam_type)
    db.commit()
    db.refresh(exam_type)
    return exam_type


def update_examination_type(db: Session, exam_type, data: dict):
    name = data.get("name")
    if name and type_name_taken(db, exam_type.school_id, name, exclude_id=exam_type.type_id):
        raise ConflictError("An examination type with this name already exists for this school")
    for k, v in data.items():
        if v is not None:
            setattr(exam_type, k, v)
    db.commit()
    db.refresh(exam_type)
    return exam_type


def list_grade_scales(db: Session, school_id: int):
    return (
        db.query(GradeScale)
        .filter(GradeScale.school_id == school_id)
        .order_by(GradeScale.name, GradeScale.version.desc())
        .all()
    )


def get_grade_scale(db: Session, school_id: int, scale_id: int):
    return (
        db.query(GradeScale)
        .filter(GradeScale.school_id == school_id, GradeScale.scale_id == scale_id)
        .first()
    )


def scale_name_taken(
    db: Session, school_id: int, name: str, exclude_id: int | None = None
) -> bool:
    query = db.query(GradeScale).filter(
        GradeScale.school_id == school_id, GradeScale.name == name
    )
    if exclude_id is not None:
        query = query.filter(GradeScale.scale_id != exclude_id)
    return query.first() is not None


def create_grade_scale(db: Session, school_id: int, data: dict):
    if scale_name_taken(db, school_id, data["name"]):
        raise ConflictError("A grade scale with this name already exists for this school")
    scale = GradeScale(school_id=school_id, **data)
    db.add(scale)
    db.commit()
    db.refresh(scale)
    return scale


def update_grade_scale(db: Session, scale, data: dict):
    name = data.get("name")
    if name and scale_name_taken(db, scale.school_id, name, exclude_id=scale.scale_id):
        raise ConflictError("A grade scale with this name already exists for this school")
    for k, v in data.items():
        if v is not None:
            setattr(scale, k, v)
    db.commit()
    db.refresh(scale)
    return scale


def new_grade_scale_version(db: Session, scale) -> GradeScale:
    """Copy-on-write: freeze the current version and open the next one.

    The old row (and its levels) stays exactly as published history
    references it; edits happen on the fresh version. The published-mark
    reference guard lands with marks.grade_level_id (Phase 4); until then
    every scale is freely versionable.
    """
    latest = (
        db.query(GradeScale.version)
        .filter(GradeScale.school_id == scale.school_id, GradeScale.name == scale.name)
        .order_by(GradeScale.version.desc())
        .first()
    )
    nxt = GradeScale(
        school_id=scale.school_id,
        name=scale.name,
        version=(latest[0] if latest else scale.version) + 1,
        supersedes_id=scale.scale_id,
        is_active=True,
    )
    db.add(nxt)
    db.flush()
    for level in list_grade_levels(db, scale.scale_id):
        db.add(GradeLevel(
            scale_id=nxt.scale_id,
            label=level.label,
            rank=level.rank,
            min_score=level.min_score,
            max_score=level.max_score,
            description=level.description,
        ))
    scale.is_active = False
    db.commit()
    db.refresh(nxt)
    return nxt


def get_grade_level_in_school(db: Session, school_id: int, level_id: int):
    """A level plus its scale, both constrained to the caller's school."""
    return (
        db.query(GradeLevel)
        .join(GradeScale, GradeScale.scale_id == GradeLevel.scale_id)
        .filter(GradeLevel.level_id == level_id, GradeScale.school_id == school_id)
        .first()
    )


def list_grade_levels(db: Session, scale_id: int):   return (
        db.query(GradeLevel)
        .filter(GradeLevel.scale_id == scale_id)
        .order_by(GradeLevel.rank, GradeLevel.label)
        .all()
    )


def level_label_taken(
    db: Session, scale_id: int, label: str, exclude_id: int | None = None
) -> bool:
    query = db.query(GradeLevel).filter(
        GradeLevel.scale_id == scale_id, GradeLevel.label == label
    )
    if exclude_id is not None:
        query = query.filter(GradeLevel.level_id != exclude_id)
    return query.first() is not None


def create_grade_level(db: Session, scale_id: int, data: dict):
    if level_label_taken(db, scale_id, data["label"]):
        raise ConflictError("This grade label already exists in this scale")
    level = GradeLevel(scale_id=scale_id, **data)
    db.add(level)
    db.commit()
    db.refresh(level)
    return level


def update_grade_level(db: Session, level, data: dict):
    label = data.get("label")
    if label and level_label_taken(db, level.scale_id, label, exclude_id=level.level_id):
        raise ConflictError("This grade label already exists in this scale")
    for k, v in data.items():
        if v is not None:
            setattr(level, k, v)
    db.commit()
    db.refresh(level)
    return level


def delete_grade_level(db: Session, level) -> None:
    db.delete(level)
    db.commit()
