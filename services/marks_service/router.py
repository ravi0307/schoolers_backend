from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, CurrentUser
from common.exceptions import ForbiddenError, NotFoundError
import repository as repo
from schemas import MarkUpsert, MarkRead

router = APIRouter(prefix="/marks", tags=["marks"])


def _master_scope(current_user: CurrentUser, school_id: int | None) -> int:
    """Master has no implicit school: every read must name one explicitly.

    Reads leave no audit trail anywhere in the system (audit covers writes
    via modified_by/at), so the explicit school_id parameter is the control:
    it forces the scope into the request instead of an ambient default.
    """
    if school_id is None:
        raise ForbiddenError("Master access requires an explicit school_id")
    return school_id


@router.get("/student/{student_id}", response_model=list[MarkRead])
def get_student_marks(
    student_id: int,
    school_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin", "master")),
):
    # Phase 0 existence-oracle fix: no unscoped existence probe runs before
    # these checks. Parent/teacher linkage checks are uniform (False for
    # unknown ids too → Forbidden); admin/master school checks return a
    # uniform NotFoundError for unknown and out-of-school ids alike, so
    # callers cannot distinguish "missing" from "another school's".
    # Non-master callers' school_id argument, if supplied, is ignored: the
    # token's school always wins.
    if current_user.role == "master":
        scope = _master_scope(current_user, school_id)
        if not repo.student_in_school(db, student_id, scope):
            raise NotFoundError("Student not found")
    elif current_user.role == "parent":
        if not current_user.linked_person_id or not repo.is_parent_of(
            db, current_user.linked_person_id, student_id
        ):
            raise ForbiddenError("You can only view marks for your own children")
    elif current_user.role == "teacher":
        if not current_user.linked_person_id or not repo.teacher_teaches_student(
            db, current_user.linked_person_id, student_id
        ):
            raise ForbiddenError("You can only view marks for students you teach")
    elif not repo.student_in_school(db, student_id, current_user.school_id):
        raise NotFoundError("Student not found")

    return repo.get_for_student(db, student_id)


@router.get("/class/{class_id}", response_model=list[MarkRead])
def get_class_marks(
    class_id: int,
    term: str | None = None,
    school_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin", "master")),
):
    # The class must belong to the caller's school before any marks are read.
    # Master names the school explicitly; everyone else is bound to the token.
    if current_user.role == "master":
        scope = _master_scope(current_user, school_id)
        if not repo.class_in_school(db, class_id, scope):
            raise NotFoundError("Class not found")
    elif current_user.school_id is not None and not repo.class_in_school(
        db, class_id, current_user.school_id
    ):
        raise NotFoundError("Class not found")

    # A teacher only sees (and can therefore only edit) marks for the
    # subjects they're actually assigned to teach in this class.
    subject_ids = None
    if current_user.role == "teacher":
        if not current_user.linked_person_id:
            raise ForbiddenError("This teacher account isn't linked to a teacher record")
        subject_ids = repo.get_subject_ids_for_class(db, current_user.linked_person_id, class_id)
    return repo.get_for_class(db, class_id, subject_ids, term)


@router.put("/{student_id}/{subject_id}", response_model=MarkRead)
def upsert_mark(
    student_id: int,
    subject_id: int,
    payload: MarkUpsert,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    # Phase 0 existence-oracle fix: the old unscoped student_exists probe is
    # gone. The subject check below is school-scoped (uniform 404 for unknown
    # and out-of-school subjects alike); the teacher linkage check is uniform
    # (False for unknown students too → Forbidden); the admin school check
    # returns a uniform NotFoundError. Master keeps no write access (403).
    if not repo.subject_exists(db, subject_id, current_user.school_id):
        raise NotFoundError("Subject not found")

    # Admin can grade anything in their own school; a teacher only what
    # they're assigned to teach.
    if current_user.role == "teacher":
        if not current_user.linked_person_id:
            raise ForbiddenError("This teacher account isn't linked to a teacher record")
        if not repo.teacher_can_grade(db, current_user.linked_person_id, student_id, subject_id):
            raise ForbiddenError("You don't teach this subject in this student's class")
    elif not repo.student_in_school(db, student_id, current_user.school_id):
        raise NotFoundError("Student not found")
    updated_by = current_user.linked_person_id if current_user.role == "teacher" else None
    return repo.upsert_mark(
        db, student_id, subject_id, payload.term, payload.score,
        updated_by, updated_by_user=current_user.user_id,
    )
