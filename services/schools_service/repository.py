from sqlalchemy.orm import Session
from sqlalchemy import func, distinct

from common.models import School, Teacher, Staff, Student, Parent


def list_schools(db: Session) -> list[School]:
    return db.query(School).filter(School.is_active.is_(True)).order_by(School.school_id).all()


def get_school(db: Session, school_id: int) -> School | None:
    return db.query(School).filter(School.school_id == school_id, School.is_active.is_(True)).first()


def create_school(db: Session, data: dict) -> School:
    school = School(**data)
    db.add(school)
    db.commit()
    db.refresh(school)
    return school


def update_school(db: Session, school: School, data: dict) -> School:
    for k, v in data.items():
        if v is not None:
            setattr(school, k, v)
    db.commit()
    db.refresh(school)
    return school


def delete_school(db: Session, school: School) -> None:
    school.is_active = False
    db.commit()


def compute_stats(db: Session, school_id: int) -> dict:
    teachers = db.query(func.count(Teacher.teacher_id)).filter(Teacher.school_id == school_id, Teacher.is_active.is_(True)).scalar()
    staff = db.query(func.count(Staff.staff_id)).filter(Staff.school_id == school_id, Staff.is_active.is_(True)).scalar()
    students = db.query(func.count(Student.student_id)).filter(Student.school_id == school_id, Student.is_active.is_(True)).scalar()
    parents = db.query(func.count(distinct(Parent.parent_id))).filter(Parent.school_id == school_id, Parent.is_active.is_(True)).scalar()
    return {"teachers": teachers, "staff": staff, "students": students, "parents": parents}
