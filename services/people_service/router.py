from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
from common.exceptions import NotFoundError
import repository as repo
from schemas import (
    TeacherCreate, TeacherUpdate, TeacherRead,
    StaffCreate, StaffUpdate, StaffRead,
    ParentCreate, ParentRead,
    StudentCreate, StudentUpdate, StudentRead,
    TeacherClassSubjectCreate,
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
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.add_teaching_assignment(db, payload.model_dump())


@router.get("/teachers/{teacher_id}/load")
def teaching_load(
    teacher_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    return repo.teaching_load(db, teacher_id)


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


@router.get("/parents/{parent_id}/children", response_model=list[StudentRead])
def children_of_parent(
    parent_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("parent", "admin")),
):
    return repo.children_of_parent(db, parent_id)


# ---- Students ----
@router.get("/students", response_model=list[StudentRead])
def list_students(
    search: str | None = Query(default=None),
    class_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    return repo.list_students(db, school_id, search, class_id)


@router.post("/students", response_model=StudentRead, status_code=201)
def create_student(
    payload: StudentCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    data = payload.model_dump()
    parent_id = data.pop("parent_id", None)
    student = repo.create_student(db, school_id, data)
    if parent_id:
        repo.link_parent_student(db, parent_id, student.student_id)
    return student


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
    return repo.update_student(db, student, payload.model_dump(exclude_unset=True))


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
