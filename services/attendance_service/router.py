from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, CurrentUser
from common.exceptions import ForbiddenError, NotFoundError
import repository as repo
from schemas import (
    AttendanceMarkBulk, AttendanceRead, ClassAttendanceSummary,
)

router = APIRouter(prefix="/attendance", tags=["attendance"])


@router.post("/mark", response_model=list[AttendanceRead])
def mark_attendance(
    payload: AttendanceMarkBulk,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    marked_by = current_user.linked_person_id if current_user.role == "teacher" else None
    return repo.mark_bulk(
        db, payload.class_id, payload.date,
        [e.model_dump() for e in payload.entries], marked_by,
    )


@router.get("", response_model=list[AttendanceRead])
def get_attendance(
    student_id: int = Query(...),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    if not repo.student_exists(db, student_id):
        raise NotFoundError("Student not found")

    if current_user.role == "parent":
        if not current_user.linked_person_id or not repo.is_parent_of(
            db, current_user.linked_person_id, student_id
        ):
            raise ForbiddenError("You can only view attendance for your own children")
    elif current_user.role == "teacher":
        if not current_user.linked_person_id or not repo.teacher_teaches_student(
            db, current_user.linked_person_id, student_id
        ):
            raise ForbiddenError("You can only view attendance for students you teach")
    elif current_user.school_id is not None and not repo.student_in_school(
        db, student_id, current_user.school_id
    ):
        raise ForbiddenError("This student is not in your school")

    return repo.get_for_student(db, student_id, date_from, date_to)


@router.get("/class/{class_id}/summary", response_model=ClassAttendanceSummary)
def class_summary(
    class_id: int,
    on_date: date = Query(..., alias="date"),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    return repo.class_summary(db, class_id, on_date)
