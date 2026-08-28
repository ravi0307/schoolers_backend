import random

from sqlalchemy.orm import Session
from sqlalchemy import or_

from common.models import Teacher, Staff, Parent, Student, ParentStudent, TeacherClassSubject


# ---- Teachers ----
def list_teachers(db: Session, school_id: int) -> list[Teacher]:
    return db.query(Teacher).filter(Teacher.school_id == school_id, Teacher.is_active.is_(True)).order_by(Teacher.name).all()


def get_teacher(db: Session, school_id: int, teacher_id: int) -> Teacher | None:
    return db.query(Teacher).filter(Teacher.school_id == school_id, Teacher.teacher_id == teacher_id, Teacher.is_active.is_(True)).first()


def create_teacher(db: Session, school_id: int, data: dict) -> Teacher:
    teacher = Teacher(school_id=school_id, **data)
    db.add(teacher)
    db.commit()
    db.refresh(teacher)
    return teacher


def update_teacher(db: Session, teacher: Teacher, data: dict) -> Teacher:
    for k, v in data.items():
        if v is not None:
            setattr(teacher, k, v)
    db.commit()
    db.refresh(teacher)
    return teacher


def delete_teacher(db: Session, teacher: Teacher) -> None:
    teacher.is_active = False
    db.commit()


def add_teaching_assignment(db: Session, data: dict) -> TeacherClassSubject:
    tcs = TeacherClassSubject(**data)
    db.add(tcs)
    db.commit()
    db.refresh(tcs)
    return tcs


def teaching_load(db: Session, teacher_id: int) -> list[TeacherClassSubject]:
    return db.query(TeacherClassSubject).filter(TeacherClassSubject.teacher_id == teacher_id).all()


# ---- Staff ----
def list_staff(db: Session, school_id: int, search: str | None = None) -> list[Staff]:
    q = db.query(Staff).filter(Staff.school_id == school_id, Staff.is_active.is_(True))
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Staff.name.ilike(like), Staff.role.ilike(like)))
    return q.order_by(Staff.name).all()


def get_staff(db: Session, school_id: int, staff_id: int) -> Staff | None:
    return db.query(Staff).filter(Staff.school_id == school_id, Staff.staff_id == staff_id, Staff.is_active.is_(True)).first()


def create_staff(db: Session, school_id: int, data: dict) -> Staff:
    staff = Staff(school_id=school_id, **data)
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return staff


def update_staff(db: Session, staff: Staff, data: dict) -> Staff:
    for k, v in data.items():
        if v is not None:
            setattr(staff, k, v)
    db.commit()
    db.refresh(staff)
    return staff


def delete_staff(db: Session, staff: Staff) -> None:
    staff.is_active = False
    db.commit()


# ---- Parents ----
def list_parents(db: Session, school_id: int) -> list[Parent]:
    return db.query(Parent).filter(Parent.school_id == school_id, Parent.is_active.is_(True)).order_by(Parent.name).all()


def create_parent(db: Session, school_id: int, data: dict) -> Parent:
    parent = Parent(school_id=school_id, **data)
    db.add(parent)
    db.commit()
    db.refresh(parent)
    return parent


def link_parent_student(db: Session, parent_id: int, student_id: int) -> None:
    db.add(ParentStudent(parent_id=parent_id, student_id=student_id, relationship_="Parent"))
    db.commit()


def children_of_parent(db: Session, parent_id: int) -> list[Student]:
    return (
        db.query(Student)
        .join(ParentStudent, ParentStudent.student_id == Student.student_id)
        .filter(ParentStudent.parent_id == parent_id, Student.is_active.is_(True))
        .all()
    )


# ---- Students ----
def list_students(db: Session, school_id: int, search: str | None = None, class_id: int | None = None) -> list[Student]:
    q = db.query(Student).filter(Student.school_id == school_id, Student.is_active.is_(True))
    if class_id:
        q = q.filter(Student.class_id == class_id)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Student.name.ilike(like), Student.admission_no.ilike(like)))
    return q.order_by(Student.name).all()


def get_student(db: Session, school_id: int, student_id: int) -> Student | None:
    return db.query(Student).filter(Student.school_id == school_id, Student.student_id == student_id, Student.is_active.is_(True)).first()


def create_student(db: Session, school_id: int, data: dict) -> Student:
    if not data.get("admission_no"):
        data["admission_no"] = f"S{school_id:03d}-{random.randint(1000,9999)}"
    student = Student(school_id=school_id, **data)
    db.add(student)
    db.commit()
    db.refresh(student)
    return student


def update_student(db: Session, student: Student, data: dict) -> Student:
    for k, v in data.items():
        if v is not None:
            setattr(student, k, v)
    db.commit()
    db.refresh(student)
    return student


def delete_student(db: Session, student: Student) -> None:
    student.is_active = False
    db.commit()
