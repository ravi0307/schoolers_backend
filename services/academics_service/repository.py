from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from common.models import (
    SchoolClass, Staff, Student, Subject, Period, Holiday, TimetableEntry,
    AcademicYear, AcademicPeriod, AcademicEvent,
    ExaminationType, GradeScale, GradeLevel,
    Examination, ExaminationSession, ExaminationSessionVersion,
    Mark, ClassSubject, StudentSubject,
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


def list_academic_periods(db: Session, year_id: int) -> list[AcademicPeriod]:
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
    data = {k: v for k, v in data.items() if k != "year_id"}
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
    # A level referenced by ANY mark cannot be removed: draft references
    # would dangle (the DB RESTRICT backstop agrees), and submitted or
    # published references are frozen history. Clear or reassign the marks
    # first, then delete. Copy-on-write new versions for scale evolution.
    refs = (
        db.query(Mark.mark_id).filter(Mark.grade_level_id == level.level_id).count()
    )
    if refs:
        raise ConflictError(
            "This grade is used by existing results and cannot be deleted"
        )
    db.delete(level)
    db.commit()


# ---- Examinations and sessions (Phase 3) ----


def exam_name_taken(
    db: Session, school_id: int, year_id: int, name: str, exclude_id: int | None = None
) -> bool:
    query = db.query(Examination).filter(
        Examination.school_id == school_id,
        Examination.year_id == year_id,
        Examination.name == name,
    )
    if exclude_id is not None:
        query = query.filter(Examination.exam_id != exclude_id)
    return query.first() is not None


def get_examination(db: Session, school_id: int, exam_id: int):
    return (
        db.query(Examination)
        .filter(Examination.school_id == school_id, Examination.exam_id == exam_id)
        .first()
    )


def list_examinations(db: Session, school_id: int, year_id: int | None = None):
    query = db.query(Examination).filter(Examination.school_id == school_id)
    if year_id is not None:
        query = query.filter(Examination.year_id == year_id)
    return query.order_by(Examination.name).all()


def create_examination(db: Session, school_id: int, user_id: int, data: dict):
    if get_examination_type(db, school_id, data["type_id"]) is None:
        raise NotFoundError("Examination type not found")
    if get_year(db, school_id, data["year_id"]) is None:
        raise NotFoundError("Academic year not found")
    if data.get("period_id") is not None and (
        get_period_in_school(db, school_id, data["period_id"]) is None
        or get_period_in_school(db, school_id, data["period_id"]).year_id != data["year_id"]
    ):
        raise NotFoundError("Academic period not found in this year")
    if data.get("grade_scale_id") is not None and (
        get_grade_scale(db, school_id, data["grade_scale_id"]) is None
    ):
        raise NotFoundError("Grade scale not found")
    if exam_name_taken(db, school_id, data["year_id"], data["name"]):
        raise ConflictError("An examination with this name already exists for this year")
    exam = Examination(
        school_id=school_id,
        created_by=user_id,
        status="draft",
        **{k: v for k, v in data.items() if k != "status"},
    )
    db.add(exam)
    db.commit()
    db.refresh(exam)
    return exam


def update_examination_draft(db: Session, exam, data: dict):
    if exam.status != "draft":
        raise ConflictError("Only draft examinations can be edited")
    if "grade_scale_id" in data and data["grade_scale_id"] is not None:
        if get_grade_scale(db, exam.school_id, data["grade_scale_id"]) is None:
            raise NotFoundError("Grade scale not found")
    name = data.get("name")
    if name and exam_name_taken(db, exam.school_id, exam.year_id, name, exclude_id=exam.exam_id):
        raise ConflictError("An examination with this name already exists for this year")
    for k, v in data.items():
        if v is not None:
            setattr(exam, k, v)
    db.commit()
    db.refresh(exam)
    return exam


def publish_examination(db: Session, exam):
    if exam.status == "published":
        return exam
    if exam.status != "draft":
        raise ConflictError("Only draft examinations can be published")
    # Any live session counts: scheduled, rescheduled (published-schedule
    # edits flip status), or completed. Only cancelled rows are dead weight.
    live = (
        db.query(ExaminationSession)
        .filter(
            ExaminationSession.exam_id == exam.exam_id,
            ExaminationSession.status != "cancelled",
        )
        .count()
    )
    if live == 0:
        raise ConflictError("An examination needs at least one scheduled session to publish")
    exam.status = "published"
    db.commit()
    db.refresh(exam)
    return exam


def unpublish_examination(db: Session, exam):
    if exam.status != "published":
        raise ConflictError("Only published examinations can be unpublished")
    exam.status = "draft"
    db.commit()
    db.refresh(exam)
    return exam


def cancel_examination(db: Session, exam, user_id: int | None, reason: str):
    """Cancel the examination and every live session, preserving all rows.

    Cancellation never deletes: sessions flip to cancelled with version rows
    so report cards keep rendering the history with its status.
    """
    for session in list_sessions(db, exam.exam_id):
        if session.status != "cancelled":
            _apply_session_change(
                db, session, {"status": "cancelled"}, user_id, reason or "Examination cancelled"
            )
    exam.status = "cancelled"
    db.commit()
    db.refresh(exam)
    return exam


def get_session_in_school(db: Session, school_id: int, session_id: int):
    return (
        db.query(ExaminationSession)
        .join(Examination, Examination.exam_id == ExaminationSession.exam_id)
        .filter(
            ExaminationSession.session_id == session_id,
            Examination.school_id == school_id,
        )
        .first()
    )


def list_sessions(db: Session, exam_id: int):
    return (
        db.query(ExaminationSession)
        .filter(ExaminationSession.exam_id == exam_id)
        .order_by(ExaminationSession.date, ExaminationSession.start_time)
        .all()
    )


def list_session_versions(db: Session, session_id: int):
    return (
        db.query(ExaminationSessionVersion)
        .filter(ExaminationSessionVersion.session_id == session_id)
        .order_by(ExaminationSessionVersion.version_id)
        .all()
    )


def _times_overlap(start_a, end_a, start_b, end_b) -> bool:
    return start_a < end_b and start_b < end_a


def _session_scope_rows(db: Session, school_id: int, on_date, exclude_session_id=None):
    """Non-cancelled same-school sessions on one date, with their exam status."""
    query = (
        db.query(ExaminationSession, Examination.status)
        .join(Examination, Examination.exam_id == ExaminationSession.exam_id)
        .filter(
            Examination.school_id == school_id,
            ExaminationSession.date == on_date,
            ExaminationSession.status != "cancelled",
        )
    )
    if exclude_session_id is not None:
        query = query.filter(ExaminationSession.session_id != exclude_session_id)
    return query.all()


def check_session_conflicts(
    db: Session,
    school_id: int,
    *,
    class_id: int,
    subject_id: int,
    on_date,
    start_time,
    end_time,
    venue: str | None,
    exam_id: int,
    exclude_session_id: int | None = None,
) -> tuple[list[str], list[str]]:
    """Transactional conflict check. Returns (blocking, warnings).

    Blocking errors refuse the write (409); warnings ride along on the
    response and are recomputed on every read, never stored.
    """
    blocking, warnings = [], []
    exam = get_examination(db, school_id, exam_id)
    if exam is None:
        raise NotFoundError("Examination not found")
    # Period/year bounds come from the examination's own academic context.
    if exam.period_id is not None:
        period = get_period_in_school(db, school_id, exam.period_id)
        if period is not None and not (period.start_date <= on_date <= period.end_date):
            blocking.append("Session date falls outside the examination period")
    year = get_year(db, school_id, exam.year_id)
    if year is not None and not (year.start_date <= on_date <= year.end_date):
        blocking.append("Session date falls outside the academic year")
    for other, other_exam_status in _session_scope_rows(
        db, school_id, on_date, exclude_session_id
    ):
        if not _times_overlap(start_time, end_time, other.start_time, other.end_time):
            continue
        if other.class_id == class_id:
            message = f"Class is already scheduled at {other.start_time}-{other.end_time}"
            (blocking if other_exam_status == "published" else warnings).append(message)
        if venue and other.venue and venue.strip().casefold() == other.venue.strip().casefold():
            message = f"Venue '{other.venue}' is already booked at {other.start_time}-{other.end_time}"
            (blocking if other_exam_status == "published" else warnings).append(message)
    taken = (
        db.query(Holiday.holiday_date, Holiday.occasion)
        .filter(Holiday.school_id == school_id, Holiday.holiday_date == on_date)
        .all()
    )
    for _, occasion in taken:
        warnings.append(f"School holiday on this date: {occasion}")
    for event in list_events(db, school_id):
        if event.start_date <= on_date <= event.end_date:
            warnings.append(f"{event.title} ({event.event_type}) spans this date")
    return blocking, warnings


def _snapshot_session(session) -> dict:
    return {
        "class_id": session.class_id,
        "subject_id": session.subject_id,
        "date": session.date.isoformat(),
        "start_time": session.start_time.isoformat(),
        "end_time": session.end_time.isoformat(),
        "venue": session.venue,
        "max_marks": session.max_marks,
        "status": session.status,
    }


def _apply_session_change(db: Session, session, changes: dict, user_id, reason: str | None):
    old = _snapshot_session(session)
    for k, v in changes.items():
        setattr(session, k, v)
    db.flush()
    db.add(ExaminationSessionVersion(
        session_id=session.session_id,
        changed_by=user_id,
        reason=reason,
        old_row=old,
        new_row=_snapshot_session(session),
    ))


def create_session(db: Session, exam, user_id: int | None, data: dict):
    data = dict(data)
    reason = data.pop("reason", None)
    if exam.status != "draft" and not reason:
        raise AppError("Adding a session to a published schedule requires a reason", status_code=400)
    if get_class(db, exam.school_id, data["class_id"]) is None:
        raise NotFoundError("Class not found")
    if get_subject(db, exam.school_id, data["subject_id"]) is None:
        raise NotFoundError("Subject not found")
    if not subject_offered(db, data["class_id"], data["subject_id"]):
        raise AppError("Subject is not offered to this class", status_code=400)
    blocking, warnings = check_session_conflicts(
        db,
        exam.school_id,
        exam_id=exam.exam_id,
        class_id=data["class_id"],
        subject_id=data["subject_id"],
        on_date=data["date"],
        start_time=data["start_time"],
        end_time=data["end_time"],
        venue=data.get("venue"),
    )
    if blocking:
        raise ConflictError("; ".join(blocking))
    session = ExaminationSession(exam_id=exam.exam_id, status="scheduled", **data)
    db.add(session)
    db.flush()
    if exam.status != "draft":
        # Adds to a published schedule are amendments too: version them with
        # a NULL old_row so History can tell an add from an edit.
        db.add(ExaminationSessionVersion(
            session_id=session.session_id,
            changed_by=user_id,
            reason=reason,
            old_row=None,
            new_row=_snapshot_session(session),
        ))
    db.commit()
    db.refresh(session)
    return session, warnings


def update_session(db: Session, exam, session, user_id: int | None, data: dict, reason: str | None):
    if exam.status != "draft" and not reason:
        raise AppError("Amending a published schedule requires a reason", status_code=400)
    changes = {k: v for k, v in data.items() if k != "reason" and v is not None}
    if "class_id" in changes and get_class(db, exam.school_id, changes["class_id"]) is None:
        raise NotFoundError("Class not found")
    if "subject_id" in changes and get_subject(db, exam.school_id, changes["subject_id"]) is None:
        raise NotFoundError("Subject not found")
    merged = {
        "class_id": changes.get("class_id", session.class_id),
        "subject_id": changes.get("subject_id", session.subject_id),
        "on_date": changes.get("date", session.date),
        "start_time": changes.get("start_time", session.start_time),
        "end_time": changes.get("end_time", session.end_time),
        "venue": changes.get("venue", session.venue),
        "exam_id": exam.exam_id,
        "exclude_session_id": session.session_id,
    }
    if not subject_offered(db, merged["class_id"], merged["subject_id"]):
        raise AppError("Subject is not offered to this class", status_code=400)
    blocking, warnings = check_session_conflicts(db, exam.school_id, **merged)
    if blocking:
        raise ConflictError("; ".join(blocking))
    # Phase 4 guard: entered scores are captured on their mark rows, so
    # lowering the session maximum below an entered score is refused here
    # rather than silently invalidating published results.
    if "max_marks" in changes:
        top = (
            db.query(func.max(Mark.score))
            .filter(
                Mark.examination_session_id == session.session_id,
                Mark.assessment_status == "present",
                Mark.score.is_not(None),
            )
            .scalar()
        )
        if top is not None and changes["max_marks"] < top:
            raise ConflictError(
                f"Maximum marks cannot drop below the highest entered score ({top})"
            )
    # Touching date/time/venue of a published session marks it rescheduled.
    if exam.status != "draft" and any(k in changes for k in ("date", "start_time", "end_time", "venue")):
        changes["status"] = "rescheduled"
    if exam.status != "draft":
        _apply_session_change(db, session, changes, user_id, reason)
    else:
        for k, v in changes.items():
            setattr(session, k, v)
    db.commit()
    db.refresh(session)
    return session, warnings


def delete_session(db: Session, exam, session) -> None:
    if exam.status != "draft":
        raise ConflictError("Only draft examination sessions can be deleted; cancel instead")
    db.delete(session)
    db.commit()


def complete_session(db: Session, exam, session, user_id: int | None):
    if session.status == "cancelled":
        raise ConflictError("A cancelled session cannot be completed")
    if session.status == "completed":
        return session, []
    if exam.status != "draft":
        _apply_session_change(db, session, {"status": "completed"}, user_id, "Session completed")
    else:
        session.status = "completed"
    db.commit()
    db.refresh(session)
    return session, []


def timetable(
    db: Session,
    school_id: int,
    year_id: int | None = None,
    class_id: int | None = None,
    date_from=None,
    date_to=None,
):
    """Session rows with their exam/class/subject names, ordered by slot."""
    query = (
        db.query(
            ExaminationSession,
            Examination.name.label("exam_name"),
            ExaminationType.name.label("exam_type_name"),
            SchoolClass.name.label("class_name"),
            Subject.name.label("subject_name"),
        )
        .join(Examination, Examination.exam_id == ExaminationSession.exam_id)
        .join(ExaminationType, ExaminationType.type_id == Examination.type_id)
        .join(SchoolClass, SchoolClass.class_id == ExaminationSession.class_id)
        .join(Subject, Subject.subject_id == ExaminationSession.subject_id)
        .filter(Examination.school_id == school_id)
    )
    if year_id is not None:
        query = query.filter(Examination.year_id == year_id)
    if class_id is not None:
        query = query.filter(ExaminationSession.class_id == class_id)
    if date_from is not None:
        query = query.filter(ExaminationSession.date >= date_from)
    if date_to is not None:
        query = query.filter(ExaminationSession.date <= date_to)
    return [
        {**_snapshot_session(s), "session_id": s.session_id, "exam_id": s.exam_id,
         "exam_name": exam_name, "exam_type_name": exam_type_name,
         "class_name": class_name, "subject_name": subject_name}
        for s, exam_name, exam_type_name, class_name, subject_name in query.order_by(
            ExaminationSession.date, ExaminationSession.start_time
        ).all()
    ]


# ---- Subject offerings and enrollment (Phase 5) ----
# Opt-in semantics: checks apply only where offering/enrollment rows exist.
# Classes and students without rows keep exact legacy behavior.


def list_offerings(db: Session, class_id: int):
    rows = (
        db.query(ClassSubject, Subject.name)
        .join(Subject, Subject.subject_id == ClassSubject.subject_id)
        .filter(ClassSubject.class_id == class_id)
        .order_by(Subject.name)
        .all()
    )
    return [
        {"class_id": class_id, "subject_id": sub, "subject_name": name, "is_required": req}
        for (off, name) in rows
        for sub, req in [(off.subject_id, off.is_required)]
    ]


def set_offering(db: Session, class_id: int, subject_id: int, is_required: bool = True):
    row = (
        db.query(ClassSubject)
        .filter(ClassSubject.class_id == class_id, ClassSubject.subject_id == subject_id)
        .first()
    )
    if row is None:
        row = ClassSubject(class_id=class_id, subject_id=subject_id, is_required=is_required)
        db.add(row)
    else:
        row.is_required = is_required
    db.commit()
    db.refresh(row)
    return row


def delete_offering(db: Session, class_id: int, subject_id: int) -> None:
    live = (
        db.query(ExaminationSession.session_id)
        .filter(
            ExaminationSession.class_id == class_id,
            ExaminationSession.subject_id == subject_id,
            ExaminationSession.status != "cancelled",
        )
        .first()
    )
    if live is not None:
        raise ConflictError("This subject has scheduled sessions and cannot be unoffered")
    db.query(ClassSubject).filter(
        ClassSubject.class_id == class_id, ClassSubject.subject_id == subject_id
    ).delete()
    db.commit()


def class_has_offerings(db: Session, class_id: int) -> bool:
    return (
        db.query(ClassSubject.class_id).filter(ClassSubject.class_id == class_id).first()
        is not None
    )


def subject_offered(db: Session, class_id: int, subject_id: int) -> bool:
    if not class_has_offerings(db, class_id):
        return True
    return (
        db.query(ClassSubject)
        .filter(ClassSubject.class_id == class_id, ClassSubject.subject_id == subject_id)
        .first()
        is not None
    )


def enrollment_class_for_year(db: Session, student_id: int, year_id: int):
    """The student's class in a year: enrollment row wins, else the cache."""
    from common.models import ClassEnrollment

    row = (
        db.query(ClassEnrollment)
        .filter(
            ClassEnrollment.student_id == student_id,
            ClassEnrollment.academic_year_id == year_id,
            ClassEnrollment.status.in_(("enrolled", "promoted", "retained", "new")),
        )
        .first()
    )
    if row is not None:
        return row.class_id
    student = db.query(Student).filter(Student.student_id == student_id).first()
    return student.class_id if student is not None else None


def list_student_subjects(db: Session, student_id: int, year_id: int | None = None):
    query = db.query(StudentSubject).filter(StudentSubject.student_id == student_id)
    if year_id is not None:
        query = query.filter(StudentSubject.academic_year_id == year_id)
    return query.all()


def enroll_student_subject(
    db: Session, student_id: int, subject_id: int, year_id: int, is_optional: bool = False
):
    existing = (
        db.query(StudentSubject)
        .filter(
            StudentSubject.student_id == student_id,
            StudentSubject.subject_id == subject_id,
            StudentSubject.academic_year_id == year_id,
        )
        .first()
    )
    if existing is not None:
        existing.state = "enrolled"
        existing.is_optional = is_optional
        db.commit()
        db.refresh(existing)
        return existing
    row = StudentSubject(
        student_id=student_id, subject_id=subject_id,
        academic_year_id=year_id, state="enrolled", is_optional=is_optional,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def student_enrolled_in(db: Session, student_id: int, subject_id: int, year_id: int) -> bool:
    """Opt-in gate for entry grids: students with NO rows for the year pass
    (school hasn't adopted enrollment); students with rows need an enrolled
    row for the subject. Non-enrolled is therefore never zero or missing."""
    any_rows = (
        db.query(StudentSubject)
        .filter(
            StudentSubject.student_id == student_id,
            StudentSubject.academic_year_id == year_id,
        )
        .first()
    )
    if any_rows is None:
        return True
    return (
        db.query(StudentSubject)
        .filter(
            StudentSubject.student_id == student_id,
            StudentSubject.subject_id == subject_id,
            StudentSubject.academic_year_id == year_id,
            StudentSubject.state == "enrolled",
        )
        .first()
        is not None
    )
