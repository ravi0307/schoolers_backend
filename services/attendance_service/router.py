from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, CurrentUser
from common.exceptions import ForbiddenError, NotFoundError
import repository as repo
from schemas import (
    AttendanceMarkBulk, AttendanceRead, ClassAttendanceSummary,
    StaffAttendanceMarkBulk, StaffAttendanceRead,
)

router = APIRouter(prefix="/attendance", tags=["attendance"])


@router.post("/mark", response_model=list[AttendanceRead])
def mark_attendance(
    payload: AttendanceMarkBulk,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    # The class must belong to the caller's school; a teacher must additionally
    # be assigned to the class. Never trust the class/student ids in the body.
    if current_user.role == "admin":
        if current_user.school_id is not None and not repo.class_in_school(
            db, payload.class_id, current_user.school_id
        ):
            raise ForbiddenError("This class is not in your school")
    else:
        if not current_user.linked_person_id:
            raise ForbiddenError("This teacher account isn't linked to a teacher record")
        if not repo.teacher_teaches_class(db, current_user.linked_person_id, payload.class_id):
            raise ForbiddenError("You can only mark attendance for classes you teach")

    # Every student on the sheet must actually be enrolled in that class.
    for entry in payload.entries:
        if not repo.student_in_class(db, entry.student_id, payload.class_id):
            raise NotFoundError(f"Student {entry.student_id} is not in this class")

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
    if current_user.role == "admin":
        if current_user.school_id is not None and not repo.class_in_school(
            db, class_id, current_user.school_id
        ):
            raise ForbiddenError("This class is not in your school")
    else:
        if not current_user.linked_person_id:
            raise ForbiddenError("This teacher account isn't linked to a teacher record")
        if not repo.teacher_teaches_class(db, current_user.linked_person_id, class_id):
            raise ForbiddenError("You can only view summaries for classes you teach")

    return repo.class_summary(db, class_id, on_date)


# ---- Staff attendance -------------------------------------------------------
# An admin accounts for staff attendance. Teachers may read it for themselves
# but never mark it, mirroring how student attendance is admin/teacher-only.
@router.post("/staff/mark", response_model=list[StaffAttendanceRead])
def mark_staff_attendance(
    payload: StaffAttendanceMarkBulk,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    school_id = current_user.school_id
    if school_id is None:
        raise ForbiddenError("Only a school admin can mark staff attendance")

    # Never trust staff ids in the body: every entry must belong to this school.
    for entry in payload.entries:
        if not repo.staff_in_school(db, entry.staff_id, school_id):
            raise NotFoundError(f"Staff member {entry.staff_id} is not in your school")

    return repo.mark_staff_bulk(
        db, school_id, payload.date,
        [e.model_dump() for e in payload.entries], current_user.user_id,
    )


@router.get("/staff", response_model=list[StaffAttendanceRead])
def get_staff_attendance(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    staff_id: int | None = Query(default=None),
    latest_only: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin", "teacher", "staff", "pilot")),
):
    school_id = current_user.school_id
    if school_id is None:
        raise ForbiddenError("No school is associated with this account")

    # A non-admin may only ever read their own row.
    if current_user.role != "admin":
        if not current_user.linked_person_id:
            raise ForbiddenError("This account isn't linked to a staff record")
        if staff_id is not None and staff_id != current_user.linked_person_id:
            raise ForbiddenError("You can only view your own attendance")
        staff_id = current_user.linked_person_id

    if latest_only:
        rows = repo.latest_staff_attendance(db, school_id)
        return [r for r in rows if staff_id is None or r["staff_id"] == staff_id]

    wanted = [staff_id] if staff_id is not None else None
    return repo.staff_attendance_rows(db, school_id, date_from, date_to, wanted)
