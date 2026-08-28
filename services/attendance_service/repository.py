from datetime import date

from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.models import Attendance


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


def class_summary(db: Session, class_id: int, the_date: date) -> dict:
    rows = db.query(Attendance.status, func.count()).filter(
        Attendance.class_id == class_id, Attendance.date == the_date
    ).group_by(Attendance.status).all()
    counts = {status: count for status, count in rows}
    present = counts.get("Present", 0)
    absent = counts.get("Absent", 0)
    return {"class_id": class_id, "date": the_date, "present": present, "absent": absent, "total": present + absent}
