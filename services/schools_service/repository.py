from sqlalchemy.orm import Session
from sqlalchemy import func, distinct

from common.email import (
    FIELD_LABELS,
    school_recipients,
    send_record_updated_email,
    send_school_removed_email,
    send_school_registered_email,
)
from common.models import School, Teacher, Staff, Student, Parent, User
from common.security import generate_temp_password, hash_password
from services.people_service import repository as people_repo


def _username_base(first_name: str, last_name: str) -> str:
    """firstname.lastname with every space removed, lowercased."""
    return f"{first_name}.{last_name}".replace(" ", "").lower()


def _unique_username(db: Session, first_name: str, last_name: str, exclude_user_id: int | None = None) -> str:
    """Return firstname.lastname, suffixed (2, 3, …) until free."""
    base = _username_base(first_name, last_name)
    candidate = base
    n = 2
    while (
        db.query(User.username)
        .filter(User.username == candidate, User.user_id != exclude_user_id)
        .first()
    ):
        candidate = f"{base}{n}"
        n += 1
    return candidate


def _provision_admin_user(db: Session, school: School, first_name: str, last_name: str) -> tuple[str, str]:
    """Create the school's admin login; returns (username, temp_password)."""
    username = _unique_username(db, first_name, last_name)
    password = generate_temp_password()
    user = User(
        school_id=school.school_id,
        role="admin",
        username=username,
        email=school.primary_email or school.alternative_email,
        password_hash=hash_password(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return username, password


def _sync_admin_user(db: Session, school: School, first_name: str, last_name: str) -> dict | None:
    """Provision or rename the school's admin login from first/last name.

    Returns fresh credentials only when a new account is created (an existing
    school that predates the feature gets one on its next edit).
    """
    user = (
        db.query(User)
        .filter(User.school_id == school.school_id, User.role == "admin", User.is_active.is_(True))
        .first()
    )
    if user is None:
        username, password = _provision_admin_user(db, school, first_name, last_name)
        return {"admin_username": username, "admin_password": password}
    email = school.primary_email or school.alternative_email
    if first_name and last_name:
        username = _unique_username(db, first_name, last_name, exclude_user_id=user.user_id)
        if username != user.username:
            user.username = username
            db.commit()
    if email and user.email != email:
        user.email = email
        db.commit()
    return None


def list_schools(db: Session) -> list[School]:
    return db.query(School).filter(School.is_active.is_(True)).order_by(School.school_id).all()


def get_school(db: Session, school_id: int) -> School | None:
    return db.query(School).filter(School.school_id == school_id, School.is_active.is_(True)).first()


def create_school(db: Session, data: dict) -> School:
    school = School(**data)
    db.add(school)
    db.commit()
    db.refresh(school)
    if school.first_name and school.last_name:
        credentials = _sync_admin_user(db, school, school.first_name, school.last_name)
        if credentials:
            school.admin_username = credentials["admin_username"]
            school.admin_password = credentials["admin_password"]
    send_school_registered_email(
        school.name,
        school_recipients(school),
        username=getattr(school, "admin_username", None),
        password=getattr(school, "admin_password", None),
    )
    return school


def update_school(db: Session, school: School, data: dict) -> School:
    first_name = data.get("first_name")
    last_name = data.get("last_name")
    old_primary = school.primary_email
    changes = []
    for k in ("primary_email", "alternative_email"):
        if k in data and data[k] is not None and str(getattr(school, k)) != str(data[k]):
            changes.append((k, getattr(school, k), data[k]))
    for k, v in data.items():
        if v is not None:
            setattr(school, k, v)
    credentials = None
    if school.first_name and school.last_name:
        credentials = _sync_admin_user(db, school, school.first_name, school.last_name)
    db.commit()
    db.refresh(school)
    if credentials:
        school.admin_username = credentials["admin_username"]
        school.admin_password = credentials["admin_password"]
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
