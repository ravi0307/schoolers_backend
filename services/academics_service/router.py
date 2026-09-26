from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
from common.exceptions import NotFoundError
import repository as repo
from schemas import (
    ClassCreate, ClassUpdate, ClassRead, SubjectRead, SubjectCreate, SubjectUpdate,
    PeriodRead, PeriodUpdate, HolidayRead, HolidayUpdate,
)

router = APIRouter(tags=["academics"])


# ---- Classes ----
@router.get("/classes", response_model=list[ClassRead])
def list_classes(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
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


# ---- Subjects (global catalog, managed by admins) ----
@router.get("/subjects", response_model=list[SubjectRead])
def list_subjects(db: Session = Depends(get_db), current_user: CurrentUser = Depends(require_role(
    "parent", "teacher", "admin", "master"
))):
    return repo.list_subjects(db)


@router.post("/subjects", response_model=SubjectRead, status_code=201)
def create_subject(
    payload: SubjectCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.create_subject(db, payload.name)


@router.patch("/subjects/{subject_id}", response_model=SubjectRead)
def update_subject(
    subject_id: int,
    payload: SubjectUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    subject = repo.get_subject(db, subject_id)
    if not subject:
        raise NotFoundError("Subject not found")
    return repo.update_subject(db, subject, payload.model_dump(exclude_unset=True))


@router.delete("/subjects/{subject_id}", status_code=204)
def delete_subject(
    subject_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    subject = repo.get_subject(db, subject_id)
    if not subject:
        raise NotFoundError("Subject not found")
    repo.delete_subject(db, subject)


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


@router.patch("/holidays/{day}", response_model=HolidayRead)
def set_holiday(
    day: str,
    payload: HolidayUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    holiday = repo.set_holiday(db, school_id, day, payload.is_holiday)
    if not holiday:
        raise NotFoundError("Holiday row not found for that day")
    return holiday
