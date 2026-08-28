from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
from common.exceptions import NotFoundError
import repository as repo
from schemas import TimetableEntryRead, TimetableEntryUpdate

router = APIRouter(prefix="/timetable", tags=["timetable"])


@router.get("/class/{class_id}", response_model=list[TimetableEntryRead])
def get_class_timetable(
    class_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    return repo.get_class_timetable(db, class_id)


@router.patch("/entry/{entry_id}", response_model=TimetableEntryRead)
def update_entry(
    entry_id: int,
    payload: TimetableEntryUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    entry = repo.get_entry(db, entry_id)
    if not entry:
        raise NotFoundError("Timetable entry not found")
    return repo.update_entry(db, entry, payload.subject_id, payload.teacher_id, school_id)


@router.patch("/entry/{entry_id}/clear-override", response_model=TimetableEntryRead)
def clear_override(
    entry_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    entry = repo.get_entry(db, entry_id)
    if not entry:
        raise NotFoundError("Timetable entry not found")
    return repo.clear_override(db, entry)
