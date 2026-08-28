from sqlalchemy.orm import Session
from sqlalchemy import func, distinct

from common.models import (
    Teacher, Staff, Student, Parent, SchoolClass, LeaveRequest, Route, Attendance,
)


def school_overview(db: Session, school_id: int) -> dict:
    teachers = db.query(func.count(Teacher.teacher_id)).filter(Teacher.school_id == school_id, Teacher.is_active.is_(True)).scalar()
    staff = db.query(func.count(Staff.staff_id)).filter(Staff.school_id == school_id, Staff.is_active.is_(True)).scalar()
    students = db.query(func.count(Student.student_id)).filter(Student.school_id == school_id, Student.is_active.is_(True)).scalar()
    parents = db.query(func.count(distinct(Parent.parent_id))).filter(Parent.school_id == school_id, Parent.is_active.is_(True)).scalar()
    classes = db.query(func.count(SchoolClass.class_id)).filter(SchoolClass.school_id == school_id, SchoolClass.is_active.is_(True)).scalar()
    pending_leave = db.query(func.count(LeaveRequest.leave_id)).filter(
        LeaveRequest.school_id == school_id, LeaveRequest.status == "Pending", LeaveRequest.is_active.is_(True)
    ).scalar()
    active_routes = db.query(func.count(Route.route_id)).filter(
        Route.school_id == school_id, Route.status == "On route", Route.is_active.is_(True)
    ).scalar()
    return {
        "school_id": school_id, "teachers": teachers, "staff": staff, "students": students,
        "parents": parents, "classes": classes, "pending_leave_requests": pending_leave,
        "active_routes": active_routes,
    }


def class_attendance_trend(db: Session, class_id: int, days: int = 14) -> list[dict]:
    rows = (
        db.query(Attendance.date, Attendance.status, func.count())
        .filter(Attendance.class_id == class_id)
        .group_by(Attendance.date, Attendance.status)
        .order_by(Attendance.date.desc())
        .limit(days * 2)
        .all()
    )
    by_date: dict[str, dict] = {}
    for the_date, status, count in rows:
        key = str(the_date)
        by_date.setdefault(key, {"date": key, "present": 0, "absent": 0})
        by_date[key]["present" if status == "Present" else "absent"] = count
    return sorted(by_date.values(), key=lambda r: r["date"])
