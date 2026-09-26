import random
import secrets

from sqlalchemy.orm import Session
from sqlalchemy import exists, or_

from common.models import (
    Teacher,
    Staff,
    Parent,
    Student,
    ParentStudent,
    TeacherClassSubject,
    RouteStudent,
    Pilot,
    SchoolClass,
    School,
    User,
    Subject,
)
from common.audit import bulk_modified_columns
from common.exceptions import ConflictError
from common.email import (
    FIELD_LABELS,
    send_record_updated_email,
    send_staff_added_email,
    send_staff_removed_email,
    send_student_removed_email,
)
from common.security import hash_password


# ---- Teachers ----
def list_teachers(db: Session, school_id: int) -> list[Teacher]:
    return db.query(Teacher).filter(Teacher.school_id == school_id, Teacher.is_active.is_(True)).order_by(Teacher.name).all()


def _fmt_value(db: Session, field: str, value) -> str:
    if field == "class_id":
        row = db.query(SchoolClass.name).filter(
            SchoolClass.class_id == int(value), SchoolClass.is_active.is_(True)
        ).first()
        return row[0] if row else str(value)
    if field == "documents":
        docs = value or []
        return f"{len(docs)} document(s)" if docs else "(none)"
    if field == "photo_url":
        return "photo set" if value else "(none)"
    if value is None:
        return "(not set)"
    return str(value)


def _changed_fields(db: Session, obj, data: dict) -> list[tuple[str, object, object]]:
    changes = []
    for k, v in data.items():
        if v is None:
            continue
        old = getattr(obj, k, None)
        if str(old) != str(v):
            changes.append((k, old, v))
    return changes


def _notify_record_update(
    db: Session,
    record_type: str,
    record_name: str,
    school_id: int,
    changes: list[tuple[str, object, object]],
    recipients: list[str],
) -> None:
    to_addrs = list(dict.fromkeys(addr.strip() for addr in recipients if addr and addr.strip()))
    if not changes or not to_addrs:
        return
    labeled = [
        (FIELD_LABELS.get(k, k.replace("_", " ").title()), _fmt_value(db, k, old), _fmt_value(db, k, new))
        for k, old, new in changes
    ]
    school = school_name(db, school_id)
    send_record_updated_email(record_type, record_name, school, labeled, to_addrs)


def get_teacher(db: Session, school_id: int, teacher_id: int) -> Teacher | None:
    return db.query(Teacher).filter(Teacher.school_id == school_id, Teacher.teacher_id == teacher_id, Teacher.is_active.is_(True)).first()


def create_teacher(db: Session, school_id: int, data: dict) -> Teacher:
    teacher = Teacher(school_id=school_id, **data)
    db.add(teacher)
    db.commit()
    db.refresh(teacher)
    return teacher


def update_teacher(db: Session, teacher: Teacher, data: dict) -> Teacher:
    changes = _changed_fields(db, teacher, data)
    old_email = teacher.email
    for k, v in data.items():
        if v is not None:
            setattr(teacher, k, v)
    db.commit()
    db.refresh(teacher)
    if changes:
        _notify_record_update(db, "Teacher", teacher.name, teacher.school_id, changes, [old_email, teacher.email])
    return teacher


def delete_teacher(db: Session, teacher: Teacher) -> None:
    teacher.is_active = False
    db.commit()


def add_teaching_assignment(db: Session, school_id: int, data: dict) -> TeacherClassSubject:
    subject = db.query(Subject).filter(
        Subject.subject_id == data["subject_id"], Subject.school_id == school_id
    ).first()
    if not subject:
        raise ConflictError("Subject does not belong to this school")

    teacher = db.query(Teacher).filter(
        Teacher.teacher_id == data["teacher_id"],
        Teacher.school_id == school_id,
        Teacher.is_active.is_(True),
    ).first()
    if not teacher:
        raise ConflictError("Teacher does not belong to this school")

    school_class = db.query(SchoolClass).filter(
        SchoolClass.class_id == data["class_id"],
        SchoolClass.school_id == school_id,
        SchoolClass.is_active.is_(True),
    ).first()
    if not school_class:
        raise ConflictError("Class does not belong to this school")

    tcs = TeacherClassSubject(**data)
    db.add(tcs)
    db.commit()
    db.refresh(tcs)
    return tcs


def teaching_load(db: Session, teacher_id: int) -> list[TeacherClassSubject]:
    return db.query(TeacherClassSubject).filter(TeacherClassSubject.teacher_id == teacher_id).all()


# ---- Staff ----
def _is_teacher_staff(staff: Staff) -> bool:
    return staff.role.strip().casefold() == "teacher"


def _linked_teacher(db: Session, staff: Staff) -> Teacher | None:
    teacher = db.query(Teacher).filter(Teacher.staff_id == staff.staff_id).first()
    if teacher:
        return teacher
    return (
        db.query(Teacher)
        .filter(
            Teacher.staff_id.is_(None),
            Teacher.school_id == staff.school_id,
            Teacher.name == staff.name,
            Teacher.phone == staff.phone,
        )
        .first()
    )


def _sync_teacher_from_staff(db: Session, staff: Staff) -> None:
    teacher = _linked_teacher(db, staff)
    if not _is_teacher_staff(staff) or not staff.is_active:
        if teacher:
            teacher.is_active = False
        return

    values = {
        "staff_id": staff.staff_id,
        "school_id": staff.school_id,
        "name": staff.name,
        "role_title": staff.role,
        "phone": staff.phone or "",
        "email": staff.email,
        "date_of_birth": staff.date_of_birth,
        "gender": staff.gender,
        "present_address": staff.present_address,
        "permanent_address": staff.permanent_address,
        "emergency_number": staff.emergency_number,
    }
    if teacher is None:
        teacher = Teacher(**values)
        db.add(teacher)
    else:
        for key, value in values.items():
            setattr(teacher, key, value)
        teacher.is_active = True


def _is_pilot_staff(staff: Staff) -> bool:
    return staff.role.strip().casefold() == "pilot"


def _linked_pilot(db: Session, staff: Staff) -> tuple[Pilot, User] | None:
    linked = (
        db.query(Pilot, User)
        .join(User, User.user_id == Pilot.user_id)
        .filter(Pilot.staff_id == staff.staff_id)
        .first()
    )
    if linked:
        return linked
    return (
        db.query(Pilot, User)
        .join(User, User.user_id == Pilot.user_id)
        .filter(
            Pilot.staff_id.is_(None),
            Pilot.school_id == staff.school_id,
            Pilot.full_name == staff.name,
            Pilot.phone == (staff.phone or ""),
        )
        .first()
    )


def _sync_pilot_from_staff(db: Session, staff: Staff) -> None:
    linked = _linked_pilot(db, staff)
    if not _is_pilot_staff(staff) or not staff.is_active:
        if linked:
            pilot, user = linked
            pilot.is_active = False
            user.is_active = False
        return

    pilot = linked[0] if linked else None
    user = linked[1] if linked else None
    if pilot is None or user is None:
        user = User(
            school_id=staff.school_id,
            role="pilot",
            username=f"pilot_{staff.school_id}_{staff.staff_id}",
            password_hash=hash_password(secrets.token_urlsafe(32)),
            is_active=True,
        )
        db.add(user)
        db.flush()
        pilot = Pilot(
            staff_id=staff.staff_id,
            user_id=user.user_id,
            school_id=staff.school_id,
            full_name=staff.name,
            email=staff.email,
            phone=staff.phone or "",
            present_address=staff.present_address,
            permanent_address=staff.permanent_address,
            aadhaar_number=staff.aadhaar_card,
            dl_number=staff.driving_license,
            is_active=True,
        )
        db.add(pilot)
        db.flush()
        user.linked_person_id = pilot.pilot_id
        return

    user.school_id = staff.school_id
    user.role = "pilot"
    user.is_active = True
    pilot.staff_id = staff.staff_id
    pilot.school_id = staff.school_id
    pilot.full_name = staff.name
    pilot.email = staff.email
    pilot.phone = staff.phone or ""
    pilot.present_address = staff.present_address
    pilot.permanent_address = staff.permanent_address
    pilot.aadhaar_number = staff.aadhaar_card
    pilot.dl_number = staff.driving_license
    pilot.is_active = True


def _sync_person_from_staff(db: Session, staff: Staff) -> None:
    _sync_teacher_from_staff(db, staff)
    _sync_pilot_from_staff(db, staff)


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
    db.flush()
    _sync_person_from_staff(db, staff)
    db.commit()
    db.refresh(staff)
    if staff.email:
        send_staff_added_email(school_name(db, school_id), staff.name, [staff.email])
    return staff


def update_staff(db: Session, staff: Staff, data: dict) -> Staff:
    changes = _changed_fields(db, staff, data)
    old_email = staff.email
    for k, v in data.items():
        if v is not None:
            setattr(staff, k, v)
    _sync_person_from_staff(db, staff)
    db.commit()
    db.refresh(staff)
    if changes:
        _notify_record_update(db, "Staff member", staff.name, staff.school_id, changes, [old_email, staff.email])
    return staff


def delete_staff(db: Session, staff: Staff) -> None:
    email = staff.email
    name = staff.name
    school = school_name(db, staff.school_id)
    staff.is_active = False
    _sync_person_from_staff(db, staff)
    db.commit()
    if email:
        send_staff_removed_email(school, name, [email])


def school_name(db: Session, school_id: int) -> str:
    row = db.query(School.name).filter(School.school_id == school_id).first()
    return row[0] if row else "your school"


def parent_email_for_student(db: Session, student_id: int) -> str | None:
    """The email of the first linked, active parent — used to notify about
    student lifecycle events (students themselves carry no email column)."""
    parent = _parent_for_student(db, student_id)
    return parent.email if parent and parent.email else None


def deactivate_school_staff(db: Session, school_id: int) -> int:
    """Mark every active staff member of a school inactive.

    Used when the school (i.e. its admin account) is removed by a master.
    Keeps each deactivated staff member's linked teacher/pilot/user records in
    sync so their login credentials are also blocked.
    """
    staff_rows = (
        db.query(Staff)
        .filter(Staff.school_id == school_id, Staff.is_active.is_(True))
        .all()
    )
    for staff in staff_rows:
        staff.is_active = False
        _sync_person_from_staff(db, staff)
    db.flush()
    return len(staff_rows)


def deactivate_school_students(db: Session, school_id: int) -> int:
    """Mark every active student of a school inactive."""
    result = (
        db.query(Student)
        .filter(Student.school_id == school_id, Student.is_active.is_(True))
        .update({Student.is_active: False, **bulk_modified_columns()}, synchronize_session=False)
    )
    db.flush()
    return int(result)


def deactivate_school_teachers(db: Session, school_id: int) -> int:
    """Mark every active teacher of a school inactive."""
    result = (
        db.query(Teacher)
        .filter(Teacher.school_id == school_id, Teacher.is_active.is_(True))
        .update({Teacher.is_active: False, **bulk_modified_columns()}, synchronize_session=False)
    )
    db.flush()
    return int(result)


def deactivate_school_parents(db: Session, school_id: int) -> int:
    """Mark every active parent of a school inactive."""
    result = (
        db.query(Parent)
        .filter(Parent.school_id == school_id, Parent.is_active.is_(True))
        .update({Parent.is_active: False, **bulk_modified_columns()}, synchronize_session=False)
    )
    db.flush()
    return int(result)


def deactivate_school_users(db: Session, school_id: int) -> int:
    """Block every login account that belongs to a school.

    Covers the school's admin, staff, teacher and parent accounts (and also the
    pilot users synced from staff rows) so nobody from a removed school can
    authenticate anymore.
    """
    result = (
        db.query(User)
        .filter(User.school_id == school_id, User.is_active.is_(True))
        .update({User.is_active: False, **bulk_modified_columns()}, synchronize_session=False)
    )
    db.flush()
    return int(result)


# ---- Parents ----
def list_parents(db: Session, school_id: int) -> list[Parent]:
    return db.query(Parent).filter(Parent.school_id == school_id, Parent.is_active.is_(True)).order_by(Parent.name).all()


def create_parent(db: Session, school_id: int, data: dict) -> Parent:
    parent = Parent(school_id=school_id, **data)
    db.add(parent)
    db.commit()
    db.refresh(parent)
    return parent


def get_parent(db: Session, school_id: int, parent_id: int) -> Parent | None:
    return db.query(Parent).filter(
        Parent.parent_id == parent_id,
        Parent.school_id == school_id,
        Parent.is_active.is_(True),
    ).first()


def _parent_for_student(db: Session, student_id: int) -> Parent | None:
    return (
        db.query(Parent)
        .join(ParentStudent, ParentStudent.parent_id == Parent.parent_id)
        .filter(
            ParentStudent.student_id == student_id,
            Parent.is_active.is_(True),
        )
        .order_by(Parent.parent_id)
        .first()
    )


def student_response(db: Session, student: Student) -> dict:
    parent = _parent_for_student(db, student.student_id)
    return {
        "student_id": student.student_id,
        "school_id": student.school_id,
        "class_id": student.class_id,
        "admission_no": student.admission_no,
        "name": student.name,
        "date_of_birth": student.date_of_birth,
        "gender": student.gender,
        "photo_url": student.photo_url,
        "aadhaar_number": student.aadhaar_number,
        "birth_certificate_number": student.birth_certificate_number,
        "documents": student.documents or [],
        "present_today": student.present_today,
        "parent_id": parent.parent_id if parent else None,
        "parent_name": parent.name if parent else None,
        "parent_phone": parent.phone if parent else None,
        "parent_email": parent.email if parent else None,
        "parent_address": parent.address if parent else None,
        "parent_emergency_number": parent.emergency_number if parent else None,
    }


def student_responses(db: Session, students: list[Student]) -> list[dict]:
    return [student_response(db, student) for student in students]


def create_student_parent(
    db: Session,
    school_id: int,
    student_id: int,
    parent_data: dict,
) -> Parent:
    parent = Parent(school_id=school_id, **parent_data)
    db.add(parent)
    db.flush()
    db.add(ParentStudent(parent_id=parent.parent_id, student_id=student_id, relationship_="Parent"))
    db.commit()
    db.refresh(parent)
    return parent


def update_student_parent(
    db: Session,
    school_id: int,
    student_id: int,
    parent_id: int | None,
    parent_data: dict,
) -> Parent | None:
    parent = _parent_for_student(db, student_id)
    if parent_id is not None:
        parent = (
            db.query(Parent)
            .filter(
                Parent.parent_id == parent_id,
                Parent.school_id == school_id,
                Parent.is_active.is_(True),
            )
            .first()
        )
        if parent is None:
            return None
        if not _parent_for_student(db, student_id):
            db.add(ParentStudent(parent_id=parent_id, student_id=student_id, relationship_="Parent"))
    elif parent is None and parent_data:
        return create_student_parent(db, school_id, student_id, parent_data)

    if parent is not None:
        for key, value in parent_data.items():
            if value is not None:
                setattr(parent, key, value)
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
def list_students(
    db: Session,
    school_id: int,
    search: str | None = None,
    class_id: int | None = None,
    unassigned_only: bool = False,
) -> list[Student]:
    q = db.query(Student).filter(Student.school_id == school_id, Student.is_active.is_(True))
    if class_id:
        q = q.filter(Student.class_id == class_id)
    if unassigned_only:
        q = q.filter(
            ~exists().where(RouteStudent.student_id == Student.student_id)
        )
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


def update_student_with_changes(
    db: Session,
    school_id: int,
    student: Student,
    data: dict,
    parent_id: int | None,
    parent_data: dict,
) -> Student:
    """Apply the student (and optional parent) edits done through the admin
    portal and email the parent(s) with the changed fields."""
    old_parent = _parent_for_student(db, student.student_id)
    old_email = old_parent.email if old_parent else None

    changes = _changed_fields(db, student, data)

    for key, value in parent_data.items():
        if value is None:
            continue
        old = getattr(old_parent, key, None) if old_parent else None
        if str(old) != str(value):
            changes.append((key, old, value))

    update_student(db, student, data)
    update_student_parent(db, school_id, student.student_id, parent_id, parent_data)

    new_parent = _parent_for_student(db, student.student_id)
    new_email = new_parent.email if new_parent else None
    if changes:
        _notify_record_update(db, "Student", student.name, school_id, changes, [old_email, new_email])
    return student


def delete_student(db: Session, student: Student) -> None:
    parent_email = parent_email_for_student(db, student.student_id)
    school = school_name(db, student.school_id)
    student.is_active = False
    db.commit()
    if parent_email:
        send_student_removed_email(school, student.name, [parent_email])
