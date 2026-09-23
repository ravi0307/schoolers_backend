from sqlalchemy.orm import Session
from sqlalchemy import func, distinct

from common.email import (
    FIELD_LABELS,
    school_recipients,
    send_record_updated_email,
    send_school_removed_email,
    send_school_registered_email,
)
from common.models import School, Teacher, Staff, Student, Parent
from services.people_service import repository as people_repo


def list_schools(db: Session) -> list[School]:
    return db.query(School).filter(School.is_active.is_(True)).order_by(School.school_id).all()


def get_school(db: Session, school_id: int) -> School | None:
    return db.query(School).filter(School.school_id == school_id, School.is_active.is_(True)).first()


def create_school(db: Session, data: dict) -> School:
    school = School(**data)
    db.add(school)
    db.commit()
    db.refresh(school)
    send_school_registered_email(school.name, school_recipients(school))
    return school


def update_school(db: Session, school: School, data: dict) -> School:
    old_primary = school.primary_email
    changes = []
    for k in ("primary_email", "alternative_email"):
        if k in data and data[k] is not None and str(getattr(school, k)) != str(data[k]):
            changes.append((k, getattr(school, k), data[k]))
    for k, v in data.items():
        if v is not None:
            setattr(school, k, v)
    db.commit()
    db.refresh(school)
    if changes:
        labeled = [
            (FIELD_LABELS.get(k, k.replace("_", " ").title()), str(old) if old else "(not set)", str(new))
            for k, old, new in changes
        ]
        recipients = list(dict.fromkeys(addr.strip() for addr in (old_primary, school.primary_email) if addr and addr.strip()))
        if recipients:
            send_record_updated_email(
                "School administrator contact",
                school.name,
                school.name,
                labeled,
                recipients,
            )
    return school


def delete_school(db: Session, school: School) -> None:
    # Removing a school must take its whole people tree offline: staff (and the
    # teacher/pilot/login records synced from them), students, teachers and
    # parents, plus every login account that belongs to the school (admin,
    # staff, teacher, parent) so nobody can authenticate anymore.
    school.is_active = False
    db.flush()
    people_repo.deactivate_school_staff(db, school.school_id)
    people_repo.deactivate_school_students(db, school.school_id)
    people_repo.deactivate_school_teachers(db, school.school_id)
    people_repo.deactivate_school_parents(db, school.school_id)
    people_repo.deactivate_school_users(db, school.school_id)
    db.commit()
    send_school_removed_email(school.name, school_recipients(school))


def compute_stats(db: Session, school_id: int) -> dict:
    teachers = db.query(func.count(Teacher.teacher_id)).filter(Teacher.school_id == school_id, Teacher.is_active.is_(True)).scalar()
    staff = db.query(func.count(Staff.staff_id)).filter(Staff.school_id == school_id, Staff.is_active.is_(True)).scalar()
    students = db.query(func.count(Student.student_id)).filter(Student.school_id == school_id, Student.is_active.is_(True)).scalar()
    parents = db.query(func.count(distinct(Parent.parent_id))).filter(Parent.school_id == school_id, Parent.is_active.is_(True)).scalar()
    return {"teachers": teachers, "staff": staff, "students": students, "parents": parents}
