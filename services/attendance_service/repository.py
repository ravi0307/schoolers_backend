from datetime import date, time

from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.models import (
    Attendance, ParentStudent, SchoolClass, Staff, StaffAttendance, Student,
    TeacherClassSubject,
)


def mark_bulk(db: Session, class_id: int, the_date: date, entries: list[dict], marked_by: int | None) -> list[Attendance]:
    """Upsert attendance for a class/date — re-marking overwrites the status."""
    results = []
    for e in entries:
        stmt = pg_insert(Attendance).values(
            student_id=e["student_id"], class_id=class_id, date=the_date,
            status=e["status"], marked_by=marked_by,
        ).on_conflict_do_update(
            index_elements=["student_id", "date"],
            set_={"status": e["status"], "class_id": class_id, "marked_by": marked_by},
        )
        db.execute(stmt)
    db.commit()
    return db.query(Attendance).filter(Attendance.class_id == class_id, Attendance.date == the_date).all()


def get_for_student(db: Session, student_id: int, date_from: date | None, date_to: date | None) -> list[Attendance]:
    q = db.query(Attendance).filter(Attendance.student_id == student_id)
    if date_from:
        q = q.filter(Attendance.date >= date_from)
    if date_to:
        q = q.filter(Attendance.date <= date_to)
    return q.order_by(Attendance.date.desc()).all()


def is_parent_of(db: Session, parent_id: int, student_id: int) -> bool:
    """True when the parent record owns this student (parent_student link)."""
    exists = db.query(ParentStudent).filter(
        ParentStudent.parent_id == parent_id,
        ParentStudent.student_id == student_id,
    ).first()
    return exists is not None


def student_exists(db: Session, student_id: int) -> bool:
    return db.query(Student.student_id).filter(Student.student_id == student_id).first() is not None


def student_in_school(db: Session, student_id: int, school_id: int) -> bool:
    return db.query(Student.student_id).filter(
        Student.student_id == student_id, Student.school_id == school_id
    ).first() is not None


def class_in_school(db: Session, class_id: int, school_id: int) -> bool:
    return db.query(SchoolClass.class_id).filter(
        SchoolClass.class_id == class_id, SchoolClass.school_id == school_id
    ).first() is not None


def student_in_class(db: Session, student_id: int, class_id: int) -> bool:
    return db.query(Student.student_id).filter(
        Student.student_id == student_id, Student.class_id == class_id
    ).first() is not None


def teacher_teaches_class(db: Session, teacher_id: int, class_id: int) -> bool:
    """A teacher may mark/call attendance in a class they teach a subject in
    or that they are the class teacher of."""
    exists = db.query(TeacherClassSubject).filter(
        TeacherClassSubject.staff_id == teacher_id,
        TeacherClassSubject.class_id == class_id,
    ).first()
    if exists is not None:
        return True
    return (
        db.query(SchoolClass.class_id)
        .filter(SchoolClass.class_id == class_id, SchoolClass.class_teacher_staff_id == teacher_id)
        .first()
        is not None
    )


def teacher_teaches_student(db: Session, teacher_id: int, student_id: int) -> bool:
    """A teacher may read attendance once they teach a subject in the
    student's class or are the class teacher of that class."""
    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        return False
    return teacher_teaches_class(db, teacher_id, student.class_id)


def class_summary(db: Session, class_id: int, the_date: date) -> dict:
    rows = db.query(Attendance.status, func.count()).filter(
        Attendance.class_id == class_id, Attendance.date == the_date
    ).group_by(Attendance.status).all()
    counts = {status: count for status, count in rows}
    present = counts.get("Present", 0)
    absent = counts.get("Absent", 0)
    return {"class_id": class_id, "date": the_date, "present": present, "absent": absent, "total": present + absent}


# ---- Staff attendance -------------------------------------------------------
def _parse_time(value) -> time | None:
    """Accept 'HH:MM' or 'HH:MM:SS' (or a time already) into a datetime.time."""
    if value is None or isinstance(value, time):
        return value
    text_value = str(value).strip()
    if not text_value:
        return None
    parts = text_value.split(":")
    hour, minute = int(parts[0]), int(parts[1])
    second = int(parts[2]) if len(parts) > 2 else 0
    return time(hour=hour, minute=minute, second=second)


def staff_in_school(db: Session, staff_id: int, school_id: int) -> bool:
    return (
        db.query(Staff.staff_id)
        .filter(Staff.staff_id == staff_id, Staff.school_id == school_id)
        .first()
        is not None
    )


def mark_staff_bulk(
    db: Session, school_id: int, the_date: date, entries: list[dict], marked_by: int | None
) -> list[dict]:
    """Upsert staff attendance for a date — re-marking overwrites the row.

    Keeps staff.name alongside each row so callers get a display-ready sheet
    without a second query.
    """
    results = []
    for e in entries:
        stmt = (
            pg_insert(StaffAttendance)
            .values(
                school_id=school_id,
                staff_id=e["staff_id"],
                date=the_date,
                status=e["status"],
                check_in=_parse_time(e.get("check_in")),
                check_out=_parse_time(e.get("check_out")),
                remarks=e.get("remarks"),
                marked_by=marked_by,
            )
            .on_conflict_do_update(
                index_elements=["staff_id", "date"],
                set_={
                    "status": e["status"],
                    "check_in": _parse_time(e.get("check_in")),
                    "check_out": _parse_time(e.get("check_out")),
                    "remarks": e.get("remarks"),
                    "marked_by": marked_by,
                },
            )
        )
        db.execute(stmt)
    db.commit()
    return staff_attendance_rows(
        db, school_id, the_date, the_date, [e["staff_id"] for e in entries] or None
    )


def staff_attendance_rows(
    db: Session,
    school_id: int,
    date_from: date | None,
    date_to: date | None,
    staff_ids: list[int] | None = None,
) -> list[dict]:
    q = db.query(
        StaffAttendance, Staff.name, Staff.person_type
    ).join(Staff, Staff.staff_id == StaffAttendance.staff_id).filter(
        StaffAttendance.school_id == school_id
    )
    if staff_ids is not None:
        if not staff_ids:
            return []
        q = q.filter(StaffAttendance.staff_id.in_(staff_ids))
    if date_from:
        q = q.filter(StaffAttendance.date >= date_from)
    if date_to:
        q = q.filter(StaffAttendance.date <= date_to)
    rows = q.order_by(StaffAttendance.date.desc(), Staff.name).all()
    return [
        {
            "attendance_id": row.attendance_id,
            "school_id": row.school_id,
            "staff_id": row.staff_id,
            "staff_name": name,
            "person_type": person_type,
            "date": row.date,
            "status": row.status,
            "check_in": row.check_in,
            "check_out": row.check_out,
            "remarks": row.remarks,
        }
        for row, name, person_type in rows
    ]


def latest_staff_attendance(db: Session, school_id: int) -> list[dict]:
    """The most recent attendance row per staff member, for a status overview."""
    subq = (
        db.query(StaffAttendance.staff_id, func.max(StaffAttendance.date).label("last_date"))
        .filter(StaffAttendance.school_id == school_id)
        .group_by(StaffAttendance.staff_id)
        .subquery()
    )
    q = (
        db.query(StaffAttendance, Staff.name, Staff.person_type)
        .join(Staff, Staff.staff_id == StaffAttendance.staff_id)
        .join(
            subq,
            (subq.c.staff_id == StaffAttendance.staff_id)
            & (subq.c.last_date == StaffAttendance.date),
        )
        .filter(StaffAttendance.school_id == school_id)
    )
    return [
        {
            "attendance_id": row.attendance_id,
            "school_id": row.school_id,
            "staff_id": row.staff_id,
            "staff_name": name,
            "person_type": person_type,
            "date": row.date,
            "status": row.status,
            "check_in": row.check_in,
            "check_out": row.check_out,
            "remarks": row.remarks,
        }
        for row, name, person_type in q.order_by(Staff.name).all()
    ]
