from datetime import date

from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.models import Attendance, ParentStudent, SchoolClass, Student, TeacherClassSubject


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
        TeacherClassSubject.teacher_id == teacher_id,
        TeacherClassSubject.class_id == class_id,
    ).first()
    if exists is not None:
        return True
    return (
        db.query(SchoolClass.class_id)
        .filter(SchoolClass.class_id == class_id, SchoolClass.class_teacher_id == teacher_id)
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
