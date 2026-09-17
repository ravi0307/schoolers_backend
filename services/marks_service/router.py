from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, CurrentUser
from common.exceptions import ForbiddenError, NotFoundError
import repository as repo
from schemas import MarkUpsert, MarkRead

router = APIRouter(prefix="/marks", tags=["marks"])


@router.get("/student/{student_id}", response_model=list[MarkRead])
def get_student_marks(
    student_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    if not repo.student_exists(db, student_id):
        raise NotFoundError("Student not found")

    if current_user.role == "parent":
        if not current_user.linked_person_id or not repo.is_parent_of(
            db, current_user.linked_person_id, student_id
        ):
            raise ForbiddenError("You can only view marks for your own children")
    elif current_user.role == "teacher":
        if not current_user.linked_person_id or not repo.teacher_teaches_student(
            db, current_user.linked_person_id, student_id
        ):
            raise ForbiddenError("You can only view marks for students you teach")
    elif current_user.school_id is not None and not repo.student_in_school(
        db, student_id, current_user.school_id
    ):
        raise ForbiddenError("This student is not in your school")

    return repo.get_for_student(db, student_id)


@router.get("/class/{class_id}", response_model=list[MarkRead])
def get_class_marks(
    class_id: int,
    term: str | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    # A teacher only sees (and can therefore only edit) marks for the
    # subjects they're actually assigned to teach in this class.
    subject_ids = None
    if current_user.role == "teacher":
        if not current_user.linked_person_id:
            raise ForbiddenError("This teacher account isn't linked to a teacher record")
        subject_ids = repo.get_subject_ids_for_class(db, current_user.linked_person_id, class_id)
    rows = repo.get_for_class(db, class_id, subject_ids)
    if term:
        rows = [row for row in rows if row.term == term]
    return rows


@router.put("/{student_id}/{subject_id}", response_model=MarkRead)
def upsert_mark(
    student_id: int,
    subject_id: int,
    payload: MarkUpsert,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    if not repo.student_exists(db, student_id):
        raise NotFoundError("Student not found")
    if not repo.subject_exists(db, subject_id):
        raise NotFoundError("Subject not found")

    # Admin can grade anything; a Teacher only what they're assigned to teach.
    if current_user.role == "teacher":
        if not current_user.linked_person_id:
            raise ForbiddenError("This teacher account isn't linked to a teacher record")
        if not repo.teacher_can_grade(db, current_user.linked_person_id, student_id, subject_id):
            raise ForbiddenError("You don't teach this subject in this student's class")
    updated_by = current_user.linked_person_id if current_user.role == "teacher" else None
    return repo.upsert_mark(
        db, student_id, subject_id, payload.term, payload.score,
        updated_by, updated_by_user=current_user.user_id,
    )
