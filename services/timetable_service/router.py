from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
from common.exceptions import NotFoundError
import repository as repo
from schemas import TimetableEntryRead, TimetableEntryUpdate, TimetableWeekCreate

router = APIRouter(prefix="/timetable", tags=["timetable"])


@router.get("/class/{class_id}", response_model=list[TimetableEntryRead])
def get_class_timetable(
    class_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    return repo.get_class_timetable(db, class_id)


@router.post("/class/{class_id}/period", response_model=list[TimetableEntryRead], status_code=201)
def create_week_period(
    class_id: int,
    payload: TimetableWeekCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    entries = repo.create_week_period(
        db,
        class_id,
        payload.period_time,
        school_id,
        current_user.user_id,
        payload.subject_id,
        payload.teacher_id,
        payload.day_of_week,
    )
    if not entries:
        raise NotFoundError("Class not found")
    return entries


@router.get("/entry/{entry_id}", response_model=TimetableEntryRead)
def get_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    entry = repo.get_entry(db, entry_id, school_id)
    if not entry:
        raise NotFoundError("Timetable entry not found")
    return entry


@router.api_route(
    "/entry/{entry_id}",
    methods=["POST", "PUT", "PATCH"],
    response_model=TimetableEntryRead,
)
def update_entry(
    entry_id: int,
    payload: TimetableEntryUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    entry = repo.get_entry(db, entry_id, school_id)
    if not entry:
        raise NotFoundError("Timetable entry not found")
    return repo.update_entry(
        db,
        entry,
        payload.subject_id,
        payload.teacher_id,
        payload.period_start_time,
        payload.period_end_time,
        school_id,
    )


@router.patch("/entry/{entry_id}/clear-override", response_model=TimetableEntryRead)
def clear_override(
    entry_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    entry = repo.get_entry(db, entry_id, current_user.school_id)
    if not entry:
        raise NotFoundError("Timetable entry not found")
    return repo.clear_override(db, entry)


@router.delete("/entry/{entry_id}")
def delete_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    entry = repo.get_entry(db, entry_id, school_id)
    if not entry:
        raise NotFoundError("Timetable entry not found")
    repo.delete_entry(db, entry)
    return {"entry_id": entry_id, "deleted": True}
