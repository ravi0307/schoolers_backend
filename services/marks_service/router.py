from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, CurrentUser
from common.exceptions import ForbiddenError
import repository as repo
from schemas import MarkUpsert, MarkRead

router = APIRouter(prefix="/marks", tags=["marks"])


@router.get("/student/{student_id}", response_model=list[MarkRead])
def get_student_marks(
    student_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    return repo.get_for_student(db, student_id)


@router.put("/{student_id}/{subject_id}", response_model=MarkRead)
def upsert_mark(
    student_id: int,
    subject_id: int,
    payload: MarkUpsert,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    # Admin can grade anything; a Teacher only what they're assigned to teach.
    if current_user.role == "teacher":
        if not current_user.linked_person_id:
            raise ForbiddenError("This teacher account isn't linked to a teacher record")
        if not repo.teacher_can_grade(db, current_user.linked_person_id, student_id, subject_id):
            raise ForbiddenError("You don't teach this subject in this student's class")
    updated_by = current_user.linked_person_id if current_user.role == "teacher" else None
    return repo.upsert_mark(db, student_id, subject_id, payload.term, payload.score, updated_by)
