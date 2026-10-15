from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
from common.exceptions import NotFoundError
import repository as repo
from schemas import (
    ClassCreate, ClassUpdate, ClassRead, SubjectRead, SubjectCreate, SubjectUpdate,
    PeriodRead, PeriodUpdate, HolidayRead, HolidayCreate, HolidayUpdate,
    AcademicYearCreate, AcademicYearUpdate, AcademicYearRead,
    AcademicPeriodCreate, AcademicPeriodUpdate, AcademicPeriodRead,
    AcademicEventCreate, AcademicEventUpdate, AcademicEventRead,
    ExaminationTypeCreate, ExaminationTypeUpdate, ExaminationTypeRead,
    GradeScaleCreate, GradeScaleUpdate, GradeScaleRead,
    GradeLevelCreate, GradeLevelUpdate, GradeLevelRead,
)

router = APIRouter(tags=["academics"])


# ---- Classes ----
@router.get("/classes", response_model=list[ClassRead])
def list_classes(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin", "staff")),
):
    return repo.list_classes(db, school_id)


@router.post("/classes", response_model=ClassRead, status_code=201)
def create_class(
    payload: ClassCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.create_class(db, school_id, payload.model_dump())


@router.patch("/classes/{class_id}", response_model=ClassRead)
def update_class(
    class_id: int,
    payload: ClassUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    cls = repo.get_class(db, school_id, class_id)
    if not cls:
        raise NotFoundError("Class not found")
    return repo.update_class(db, cls, payload.model_dump(exclude_unset=True))


@router.delete("/classes/{class_id}", status_code=204)
def delete_class(
    class_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    cls = repo.get_class(db, school_id, class_id)
    if not cls:
        raise NotFoundError("Class not found")
    repo.delete_class(db, cls)


# ---- Subjects (per-school catalog, managed by admins) ----
@router.get("/subjects", response_model=list[SubjectRead])
def list_subjects(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    return repo.list_subjects(db, school_id)


@router.post("/subjects", response_model=SubjectRead, status_code=201)
def create_subject(
    payload: SubjectCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.create_subject(db, school_id, payload.name)


@router.patch("/subjects/{subject_id}", response_model=SubjectRead)
def update_subject(
    subject_id: int,
    payload: SubjectUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    subject = repo.get_subject(db, school_id, subject_id)
    if not subject:
        raise NotFoundError("Subject not found")
    return repo.update_subject(db, subject, payload.model_dump(exclude_unset=True))


@router.patch("/subjects/{subject_id}/deactivate", response_model=SubjectRead)
def deactivate_subject(
    subject_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    subject = repo.get_subject(db, school_id, subject_id)
    if not subject:
        raise NotFoundError("Subject not found")
    return repo.deactivate_subject(db, subject)


@router.patch("/subjects/{subject_id}/activate", response_model=SubjectRead)
def activate_subject(
    subject_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    subject = repo.get_subject(db, school_id, subject_id)
    if not subject:
        raise NotFoundError("Subject not found")
    return repo.activate_subject(db, subject)


# ---- Periods (global lookup, editable by admin) ----
@router.get("/periods", response_model=list[PeriodRead])
def list_periods(db: Session = Depends(get_db), current_user: CurrentUser = Depends(require_role(
    "parent", "teacher", "admin"
))):
    return repo.list_periods(db)


@router.patch("/periods/{period_id}", response_model=PeriodRead)
def update_period(
    period_id: int,
    payload: PeriodUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin", "teacher")),
):
    period = repo.update_period(
        db,
        period_id,
        school_id,
        payload.period_time,
        payload.period_start_time,
        payload.period_end_time,
    )
    if not period:
        raise NotFoundError("Period not found")
    return period


# ---- Holidays ----
@router.get("/holidays", response_model=list[HolidayRead])
def list_holidays(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    return repo.list_holidays(db, school_id)


@router.post("/holidays", response_model=list[HolidayRead], status_code=201)
def create_holiday(
    payload: HolidayCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    """Add one named holiday, on a single day or across a span of them.

    Returns every day created, so a caller that asked for a range can confirm
    exactly how wide the break ended up. A one-day request still returns one row.
    """
    return repo.create_holiday_range(
        db, school_id, payload.occasion, payload.holiday_date, payload.end_date
    )


@router.patch("/holidays/{holiday_id}", response_model=list[HolidayRead])
def update_holiday(
    holiday_id: int,
    payload: HolidayUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    """Reschedule or rename a holiday, along with the rest of its break.

    Scoped to the caller's own school, so another school's holiday_id is a 404
    rather than an edit. Returns the days the break now occupies.
    """
    holiday = repo.get_holiday(db, school_id, holiday_id)
    if holiday is None:
        raise NotFoundError("Holiday not found")
    return repo.update_holiday_range(
        db, holiday, payload.occasion, payload.holiday_date, payload.end_date
    )


@router.delete("/holidays/{holiday_id}", status_code=204)
def delete_holiday(
    holiday_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    """Remove a holiday and every other day of the same break."""
    holiday = repo.get_holiday(db, school_id, holiday_id)
    if holiday is None:
        raise NotFoundError("Holiday not found")
    repo.delete_holiday_group(db, holiday)
    return Response(status_code=204)


# ---- Academic calendar (Phase 1): years, periods, dated events ----


@router.get("/academic-years", response_model=list[AcademicYearRead])
def list_academic_years(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    return repo.list_years(db, school_id)


@router.post("/academic-years", response_model=AcademicYearRead, status_code=201)
def create_academic_year(
    payload: AcademicYearCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.create_year(db, school_id, payload.model_dump())


@router.patch("/academic-years/{year_id}", response_model=AcademicYearRead)
def update_academic_year(
    year_id: int,
    payload: AcademicYearUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    year = repo.get_year(db, school_id, year_id)
    if year is None:
        raise NotFoundError("Academic year not found")
    return repo.update_year(db, year, payload.model_dump(exclude_unset=True))


@router.get("/academic-periods", response_model=list[AcademicPeriodRead])
def list_academic_periods(
    year_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    # The year must belong to the caller's school before its periods are read.
    if repo.get_year(db, school_id, year_id) is None:
        raise NotFoundError("Academic year not found")
    return repo.list_periods(db, year_id)


@router.post("/academic-periods", response_model=AcademicPeriodRead, status_code=201)
def create_academic_period(
    payload: AcademicPeriodCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    year = repo.get_year(db, school_id, payload.year_id)
    if year is None:
        raise NotFoundError("Academic year not found")
    return repo.create_period(db, year, payload.model_dump(exclude_unset=True))


@router.patch("/academic-periods/{period_id}", response_model=AcademicPeriodRead)
def update_academic_period(
    period_id: int,
    payload: AcademicPeriodUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    period = repo.get_period_in_school(db, school_id, period_id)
    if period is None:
        raise NotFoundError("Academic period not found")
    return repo.update_period_entry(db, period, payload.model_dump(exclude_unset=True))


@router.get("/academic-events", response_model=list[AcademicEventRead])
def list_academic_events(
    year_id: int | None = None,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    if year_id is not None and repo.get_year(db, school_id, year_id) is None:
        raise NotFoundError("Academic year not found")
    return repo.list_events(db, school_id, year_id)


@router.post("/academic-events", response_model=AcademicEventRead, status_code=201)
def create_academic_event(
    payload: AcademicEventCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.create_event(db, school_id, payload.model_dump(exclude_unset=True))


@router.patch("/academic-events/{event_id}", response_model=AcademicEventRead)
def update_academic_event(
    event_id: int,
    payload: AcademicEventUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    event = repo.get_event(db, school_id, event_id)
    if event is None:
        raise NotFoundError("Academic event not found")
    return repo.update_event(db, event, payload.model_dump(exclude_unset=True))


@router.delete("/academic-events/{event_id}", status_code=204)
def delete_academic_event(
    event_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    event = repo.get_event(db, school_id, event_id)
    if event is None:
        raise NotFoundError("Academic event not found")
    repo.delete_event(db, event)
    return Response(status_code=204)


# ---- Examination setup (Phase 2): types, grade scales ----


@router.get("/examination-types", response_model=list[ExaminationTypeRead])
def list_examination_types(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    return repo.list_examination_types(db, school_id)


@router.post("/examination-types", response_model=ExaminationTypeRead, status_code=201)
def create_examination_type(
    payload: ExaminationTypeCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.create_examination_type(db, school_id, payload.model_dump(exclude_unset=True))


@router.patch("/examination-types/{type_id}", response_model=ExaminationTypeRead)
def update_examination_type(
    type_id: int,
    payload: ExaminationTypeUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    exam_type = repo.get_examination_type(db, school_id, type_id)
    if exam_type is None:
        raise NotFoundError("Examination type not found")
    return repo.update_examination_type(db, exam_type, payload.model_dump(exclude_unset=True))


@router.get("/grade-scales", response_model=list[GradeScaleRead])
def list_grade_scales(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    return repo.list_grade_scales(db, school_id)


@router.post("/grade-scales", response_model=GradeScaleRead, status_code=201)
def create_grade_scale(
    payload: GradeScaleCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.create_grade_scale(db, school_id, payload.model_dump(exclude_unset=True))


@router.patch("/grade-scales/{scale_id}", response_model=GradeScaleRead)
def update_grade_scale(
    scale_id: int,
    payload: GradeScaleUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    scale = repo.get_grade_scale(db, school_id, scale_id)
    if scale is None:
        raise NotFoundError("Grade scale not found")
    return repo.update_grade_scale(db, scale, payload.model_dump(exclude_unset=True))


@router.post("/grade-scales/{scale_id}/new-version", response_model=GradeScaleRead, status_code=201)
def new_grade_scale_version(
    scale_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    """Copy-on-write: freeze the current version, open the next with copied levels."""
    scale = repo.get_grade_scale(db, school_id, scale_id)
    if scale is None:
        raise NotFoundError("Grade scale not found")
    return repo.new_grade_scale_version(db, scale)


@router.get("/grade-scales/{scale_id}/levels", response_model=list[GradeLevelRead])
def list_grade_levels(
    scale_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    if repo.get_grade_scale(db, school_id, scale_id) is None:
        raise NotFoundError("Grade scale not found")
    return repo.list_grade_levels(db, scale_id)


@router.post("/grade-scales/{scale_id}/levels", response_model=GradeLevelRead, status_code=201)
def create_grade_level(
    scale_id: int,
    payload: GradeLevelCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    if repo.get_grade_scale(db, school_id, scale_id) is None:
        raise NotFoundError("Grade scale not found")
    return repo.create_grade_level(db, scale_id, payload.model_dump(exclude_unset=True))


@router.patch("/grade-levels/{level_id}", response_model=GradeLevelRead)
def update_grade_level(
    level_id: int,
    payload: GradeLevelUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    level = repo.get_grade_level_in_school(db, school_id, level_id)
    if level is None:
        raise NotFoundError("Grade level not found")
    return repo.update_grade_level(db, level, payload.model_dump(exclude_unset=True))


@router.delete("/grade-levels/{level_id}", status_code=204)
def delete_grade_level(
    level_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    level = repo.get_grade_level_in_school(db, school_id, level_id)
    if level is None:
        raise NotFoundError("Grade level not found")
    repo.delete_grade_level(db, level)
    return Response(status_code=204)
