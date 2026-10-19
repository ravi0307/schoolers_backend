from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_examinations_enabled, require_school_scope, CurrentUser
from common.email import send_student_added_email
from common.exceptions import AppError, NotFoundError, ForbiddenError
import repository as repo
from schemas import (
    TeacherCreate, TeacherUpdate, TeacherRead,
    StaffCreate, StaffUpdate, StaffRead,
    ParentCreate, ParentRead,
    StudentCreate, StudentUpdate, StudentRead,
    TeacherClassSubjectCreate,
    PromotionPreview, PromotionCommit, PromotionBatchRead,
    StudentSubjectEnroll, StudentSubjectUpdate, StudentSubjectRead,
)

router = APIRouter(tags=["people"])


# ---- Teachers ----
@router.get("/teachers", response_model=list[TeacherRead])
def list_teachers(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    return repo.list_teachers(db, school_id)


@router.post("/teachers", response_model=TeacherRead, status_code=201)
def create_teacher(
    payload: TeacherCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.create_teacher(db, school_id, payload.model_dump())


@router.patch("/teachers/{teacher_id}", response_model=TeacherRead)
def update_teacher(
    teacher_id: int,
    payload: TeacherUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    teacher = repo.get_teacher(db, school_id, teacher_id)
    if not teacher:
        raise NotFoundError("Teacher not found")
    return repo.update_teacher(db, teacher, payload.model_dump(exclude_unset=True))


@router.delete("/teachers/{teacher_id}", status_code=204)
def delete_teacher(
    teacher_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    teacher = repo.get_teacher(db, school_id, teacher_id)
    if not teacher:
        raise NotFoundError("Teacher not found")
    repo.delete_teacher(db, teacher)


@router.post("/teachers/assignments", status_code=201)
def assign_teaching_load(
    payload: TeacherClassSubjectCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    tcs = repo.add_teaching_assignment(db, school_id, payload.model_dump())
    return {
        "id": tcs.id,
        "staff_id": tcs.staff_id,
        "teacher_id": tcs.staff_id,
        "class_id": tcs.class_id,
        "subject_id": tcs.subject_id,
        "is_class_teacher": tcs.is_class_teacher,
    }


@router.get("/teachers/{teacher_id}/load")
def teaching_load(
    teacher_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    # Teachers may only read their own load; admins only inside their own
    # school (same tenancy rule as children_of_parent below).
    if current_user.role == "teacher":
        if not current_user.linked_person_id or current_user.linked_person_id != teacher_id:
            raise ForbiddenError("Teachers can only view their own teaching load")
    elif current_user.school_id is None or not repo.get_teacher(db, current_user.school_id, teacher_id):
        raise NotFoundError("Teacher not found")
    return [
        {
            "id": tcs.id,
            "staff_id": tcs.staff_id,
            "teacher_id": tcs.staff_id,
            "class_id": tcs.class_id,
            "subject_id": tcs.subject_id,
            "is_class_teacher": tcs.is_class_teacher,
        }
        for tcs in repo.teaching_load(db, teacher_id)
    ]


# ---- Staff ----
@router.get("/staff", response_model=list[StaffRead])
def list_staff(
    search: str | None = Query(default=None),
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.list_staff(db, school_id, search)


@router.post("/staff", response_model=StaffRead, status_code=201)
def create_staff(
    payload: StaffCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.create_staff(db, school_id, payload.model_dump())


@router.patch("/staff/{staff_id}", response_model=StaffRead)
def update_staff(
    staff_id: int,
    payload: StaffUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    staff = repo.get_staff(db, school_id, staff_id)
    if not staff:
        raise NotFoundError("Staff member not found")
    return repo.update_staff(db, staff, payload.model_dump(exclude_unset=True))


@router.delete("/staff/{staff_id}", status_code=204)
def delete_staff(
    staff_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    staff = repo.get_staff(db, school_id, staff_id)
    if not staff:
        raise NotFoundError("Staff member not found")
    repo.delete_staff(db, staff)


# ---- Parents ----
@router.get("/parents", response_model=list[ParentRead])
def list_parents(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.list_parents(db, school_id)


@router.post("/parents", response_model=ParentRead, status_code=201)
def create_parent(
    payload: ParentCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.create_parent(db, school_id, payload.model_dump())


@router.get("/parents/me/children", response_model=list[StudentRead])
def my_children(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("parent")),
):
    if not current_user.linked_person_id:
        raise ForbiddenError("This parent account isn't linked to a parent record")
    return repo.student_responses(db, repo.children_of_parent(db, current_user.linked_person_id))


@router.get("/parents/{parent_id}/children", response_model=list[StudentRead])
def children_of_parent(
    parent_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "admin")),
):
    # A parent may only list their own children; admins may look up any parent
    # inside their own school.
    if current_user.role == "parent":
        if not current_user.linked_person_id:
            raise ForbiddenError("This parent account isn't linked to a parent record")
        parent_id = current_user.linked_person_id
    elif not repo.get_parent(db, school_id, parent_id):
        raise NotFoundError("Parent not found")
    return repo.student_responses(db, repo.children_of_parent(db, parent_id))


# ---- Students ----
@router.get("/students", response_model=list[StudentRead])
def list_students(
    search: str | None = Query(default=None),
    class_id: int | None = Query(default=None),
    unassigned_only: bool = Query(default=False),
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    return repo.student_responses(
        db,
        repo.list_students(db, school_id, search, class_id, unassigned_only),
    )


@router.post("/students", response_model=StudentRead, status_code=201)
def create_student(
    payload: StudentCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    data = payload.model_dump()
    parent_id = data.pop("parent_id", None)
    parent_data = {
        "name": data.pop("parent_name", None),
        "phone": data.pop("parent_phone", None),
        "email": data.pop("parent_email", None),
        "address": data.pop("parent_address", None),
        "emergency_number": data.pop("parent_emergency_number", None),
    }
    parent_data = {key: value for key, value in parent_data.items() if value is not None}
    if parent_data and ("name" not in parent_data or "phone" not in parent_data):
        raise AppError("parent_name and parent_phone are required for a new parent")
    student = repo.create_student(db, school_id, data)
    if parent_id:
        repo.link_parent_student(db, parent_id, student.student_id)
    elif parent_data:
        repo.create_student_parent(db, school_id, student.student_id, parent_data)
    parent_email = repo.parent_email_for_student(db, student.student_id)
    if parent_email:
        send_student_added_email(repo.school_name(db, school_id), student.name, [parent_email])
    return repo.student_response(db, student)


@router.patch("/students/{student_id}", response_model=StudentRead)
def update_student(
    student_id: int,
    payload: StudentUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    student = repo.get_student(db, school_id, student_id)
    if not student:
        raise NotFoundError("Student not found")
    data = payload.model_dump(exclude_unset=True)
    parent_id = data.pop("parent_id", None)
    parent_data = {
        "name": data.pop("parent_name", None),
        "phone": data.pop("parent_phone", None),
        "email": data.pop("parent_email", None),
        "address": data.pop("parent_address", None),
        "emergency_number": data.pop("parent_emergency_number", None),
    }
    parent_data = {key: value for key, value in parent_data.items() if value is not None}
    repo.update_student_with_changes(db, school_id, student, data, parent_id, parent_data)
    return repo.student_response(db, student)


@router.delete("/students/{student_id}", status_code=204)
def delete_student(
    student_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    student = repo.get_student(db, school_id, student_id)
    if not student:
        raise NotFoundError("Student not found")
    repo.delete_student(db, student)


# ---- Promotion and carry-forward (Phase 5) ----


@router.post("/promotions/preview")
def preview_promotion(
    payload: PromotionPreview,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    """Dry run: proposed moves plus exceptions. Writes nothing.

    With no source year and an empty map this is the founding-year
    reconciliation roster: every active student proposed into their
    current class as `enrolled` in the target year.
    """
    require_examinations_enabled(db, school_id)
    class_map = {entry.from_class_id: entry.to_class_id for entry in payload.class_map}
    return repo.preview_promotion(
        db, school_id, payload.source_year_id, payload.target_year_id, class_map
    )


@router.post("/promotions/commit")
def commit_promotion(
    payload: PromotionCommit,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    """Apply enrollment rows plus the class-cache update in one transaction.
    Retries with the same idempotency key return the prior receipt."""
    require_examinations_enabled(db, school_id)
    return repo.commit_promotion(
        db, school_id, current_user.user_id,
        payload.source_year_id, payload.target_year_id,
        [a.model_dump() for a in payload.assignments],
        payload.idempotency_key,
    )


@router.get("/promotions/batches", response_model=list[PromotionBatchRead])
def list_promotion_batches(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    require_examinations_enabled(db, school_id)
    batches = repo.list_promotion_batches(db, school_id)
    out = []
    for batch in batches:
        receipt = repo._promotion_receipt(db, batch, duplicate=False)
        out.append({
            "batch_id": batch.batch_id,
            "school_id": batch.school_id,
            "source_year_id": batch.source_year_id,
            "target_year_id": batch.target_year_id,
            "status": batch.status,
            "applied": receipt["applied"],
        })
    return out


@router.get("/promotions/batches/{batch_id}")
def get_promotion_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    require_examinations_enabled(db, school_id)
    batch = repo.get_promotion_batch(db, school_id, batch_id)
    if batch is None:
        raise NotFoundError("Promotion batch not found")
    return repo._promotion_receipt(db, batch, duplicate=False)
@router.get("/students/{student_id}/subjects", response_model=list[StudentSubjectRead])
def list_student_subjects(
    student_id: int,
    year_id: int | None = None,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    require_examinations_enabled(db, school_id)
    from services.academics_service import repository as acad_repo

    if repo.get_student(db, school_id, student_id) is None:
        raise NotFoundError("Student not found")
    rows = acad_repo.list_student_subjects(db, student_id, year_id)
    subjects = {s.subject_id: s.name for s in acad_repo.list_subjects(db, school_id)}
    return [
        {
            "student_id": r.student_id,
            "subject_id": r.subject_id,
            "subject_name": subjects.get(r.subject_id),
            "academic_year_id": r.academic_year_id,
            "state": r.state,
            "is_optional": r.is_optional,
        }
        for r in rows
    ]


@router.post("/students/{student_id}/subjects", response_model=StudentSubjectRead, status_code=201)
def enroll_student_subject(
    student_id: int,
    payload: StudentSubjectEnroll,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    require_examinations_enabled(db, school_id)
    from services.academics_service import repository as acad_repo

    if repo.get_student(db, school_id, student_id) is None:
        raise NotFoundError("Student not found")
    if acad_repo.get_subject(db, school_id, payload.subject_id) is None:
        raise NotFoundError("Subject not found")
    if acad_repo.get_year(db, school_id, payload.year_id) is None:
        raise NotFoundError("Academic year not found")
    row = acad_repo.enroll_student_subject(
        db, student_id, payload.subject_id, payload.year_id, payload.is_optional
    )
    return {
        "student_id": row.student_id,
        "subject_id": row.subject_id,
        "subject_name": acad_repo.get_subject(db, school_id, row.subject_id).name,
        "academic_year_id": row.academic_year_id,
        "state": row.state,
        "is_optional": row.is_optional,
    }


@router.patch("/students/{student_id}/subjects/{subject_id}", response_model=StudentSubjectRead)
def update_student_subject(
    student_id: int,
    subject_id: int,
    payload: StudentSubjectUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    require_examinations_enabled(db, school_id)
    from services.academics_service import repository as acad_repo
    from common.models import StudentSubject as StudentSubjectModel

    if repo.get_student(db, school_id, student_id) is None:
        raise NotFoundError("Student not found")
    row = (
        db.query(StudentSubjectModel)
        .filter(
            StudentSubjectModel.student_id == student_id,
            StudentSubjectModel.subject_id == subject_id,
        )
        .order_by(StudentSubjectModel.academic_year_id.desc())
        .first()
    )
    if row is None:
        raise NotFoundError("Subject enrollment not found")
    row.state = payload.state
    db.commit()
    db.refresh(row)
    return {
        "student_id": row.student_id,
        "subject_id": row.subject_id,
        "subject_name": acad_repo.get_subject(db, school_id, row.subject_id).name,
        "academic_year_id": row.academic_year_id,
        "state": row.state,
        "is_optional": row.is_optional,
    }
