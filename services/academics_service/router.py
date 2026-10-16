from datetime import date

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
from common.exceptions import ConflictError, NotFoundError
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
    ExaminationCreate, ExaminationUpdate, ExaminationRead, ExaminationCancel,
    SessionCreate, SessionUpdate, SessionRead, SessionWriteResult,
    SessionTimetableRow, SessionVersionRead,
    ClassSubjectSet, ClassSubjectRead,
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


# ---- Examinations and sessions (Phase 3) ----


@router.post("/examinations", response_model=ExaminationRead, status_code=201)
def create_examination(
    payload: ExaminationCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.create_examination(
        db, school_id, current_user.user_id, payload.model_dump(exclude_unset=True)
    )


@router.get("/examinations", response_model=list[ExaminationRead])
def list_examinations(
    year_id: int | None = None,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    if year_id is not None and repo.get_year(db, school_id, year_id) is None:
        raise NotFoundError("Academic year not found")
    return repo.list_examinations(db, school_id, year_id)


@router.get("/examinations/timetable", response_model=list[SessionTimetableRow])
def examination_timetable(
    year_id: int | None = None,
    class_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    # NOTE: defined before /examinations/{exam_id} so "timetable" is not
    # captured as an exam id.
    return repo.timetable(db, school_id, year_id, class_id, date_from, date_to)


@router.get("/examinations/{exam_id}", response_model=ExaminationRead)
def get_examination(
    exam_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    exam = repo.get_examination(db, school_id, exam_id)
    if exam is None:
        raise NotFoundError("Examination not found")
    return exam


@router.patch("/examinations/{exam_id}", response_model=ExaminationRead)
def update_examination(
    exam_id: int,
    payload: ExaminationUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    exam = repo.get_examination(db, school_id, exam_id)
    if exam is None:
        raise NotFoundError("Examination not found")
    return repo.update_examination_draft(db, exam, payload.model_dump(exclude_unset=True))


@router.post("/examinations/{exam_id}/publish", response_model=ExaminationRead)
def publish_examination(
    exam_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    exam = repo.get_examination(db, school_id, exam_id)
    if exam is None:
        raise NotFoundError("Examination not found")
    return repo.publish_examination(db, exam)


@router.post("/examinations/{exam_id}/unpublish", response_model=ExaminationRead)
def unpublish_examination(
    exam_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    exam = repo.get_examination(db, school_id, exam_id)
    if exam is None:
        raise NotFoundError("Examination not found")
    return repo.unpublish_examination(db, exam)


@router.post("/examinations/{exam_id}/cancel", response_model=ExaminationRead)
def cancel_examination(
    exam_id: int,
    payload: ExaminationCancel,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    exam = repo.get_examination(db, school_id, exam_id)
    if exam is None:
        raise NotFoundError("Examination not found")
    return repo.cancel_examination(db, exam, current_user.user_id, payload.reason)


@router.delete("/examinations/{exam_id}", status_code=204)
def delete_examination(
    exam_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    exam = repo.get_examination(db, school_id, exam_id)
    if exam is None:
        raise NotFoundError("Examination not found")
    if exam.status != "draft":
        raise ConflictError("Only draft examinations can be deleted; cancel instead")
    db.delete(exam)
    db.commit()
    return Response(status_code=204)


@router.get("/examinations/{exam_id}/sessions", response_model=list[SessionRead])
def list_examination_sessions(
    exam_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    if repo.get_examination(db, school_id, exam_id) is None:
        raise NotFoundError("Examination not found")
    return repo.list_sessions(db, exam_id)


@router.post("/examinations/{exam_id}/sessions", response_model=SessionWriteResult, status_code=201)
def create_examination_session(
    exam_id: int,
    payload: SessionCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    exam = repo.get_examination(db, school_id, exam_id)
    if exam is None:
        raise NotFoundError("Examination not found")
    session, warnings = repo.create_session(
        db, exam, current_user.user_id, payload.model_dump(exclude_unset=True)
    )
    return {"session": session, "warnings": warnings}


def _session_in_scope(db: Session, school_id: int, session_id: int):
    session = repo.get_session_in_school(db, school_id, session_id)
    if session is None:
        raise NotFoundError("Examination session not found")
    exam = repo.get_examination(db, school_id, session.exam_id)
    if exam is None:  # Cannot happen while the FK holds; guarded regardless.
        raise NotFoundError("Examination not found")
    return session, exam


@router.patch("/sessions/{session_id}", response_model=SessionWriteResult)
def update_examination_session(
    session_id: int,
    payload: SessionUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    session, exam = _session_in_scope(db, school_id, session_id)
    data = payload.model_dump(exclude_unset=True)
    session, warnings = repo.update_session(
        db, exam, session, current_user.user_id, data, data.get("reason")
    )
    return {"session": session, "warnings": warnings}


@router.delete("/sessions/{session_id}", status_code=204)
def delete_examination_session(
    session_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    session, exam = _session_in_scope(db, school_id, session_id)
    repo.delete_session(db, exam, session)
    return Response(status_code=204)


@router.post("/sessions/{session_id}/complete", response_model=SessionWriteResult)
def complete_examination_session(
    session_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    session, exam = _session_in_scope(db, school_id, session_id)
    session, warnings = repo.complete_session(db, exam, session, current_user.user_id)
    return {"session": session, "warnings": warnings}


@router.get("/sessions/{session_id}/versions", response_model=list[SessionVersionRead])
def list_session_versions(
    session_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    session, _ = _session_in_scope(db, school_id, session_id)
    return repo.list_session_versions(db, session.session_id)


# ---- Subject offerings and enrollment (Phase 5) ----


@router.get("/classes/{class_id}/subjects", response_model=list[ClassSubjectRead])
def list_class_offerings(
    class_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    if repo.get_class(db, school_id, class_id) is None:
        raise NotFoundError("Class not found")
    return repo.list_offerings(db, class_id)


@router.post("/classes/{class_id}/subjects", response_model=ClassSubjectRead, status_code=201)
def set_class_offering(
    class_id: int,
    payload: ClassSubjectSet,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    if repo.get_class(db, school_id, class_id) is None:
        raise NotFoundError("Class not found")
    if repo.get_subject(db, school_id, payload.subject_id) is None:
        raise NotFoundError("Subject not found")
    row = repo.set_offering(db, class_id, payload.subject_id, payload.is_required)
    return {
        "class_id": class_id,
        "subject_id": row.subject_id,
        "subject_name": repo.get_subject(db, school_id, row.subject_id).name,
        "is_required": row.is_required,
    }


@router.delete("/classes/{class_id}/subjects/{subject_id}", status_code=204)
def delete_class_offering(
    class_id: int,
    subject_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    if repo.get_class(db, school_id, class_id) is None:
        raise NotFoundError("Class not found")
    repo.delete_offering(db, class_id, subject_id)
    return Response(status_code=204)


