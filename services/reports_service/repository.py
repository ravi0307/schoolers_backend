from sqlalchemy.orm import Session
from sqlalchemy import func, distinct

from common.models import (
    Staff, Student, Parent, ParentStudent, SchoolClass, Subject, LeaveRequest,
    Route, Attendance, Mark,
)


def school_overview(db: Session, school_id: int) -> dict:
    # Teachers are staff rows, so "staff" counts the non-teaching employees to
    # keep the two figures disjoint rather than double counting.
    teachers = db.query(func.count(Staff.staff_id)).filter(
        Staff.school_id == school_id,
        Staff.person_type == "teacher",
        Staff.is_active.is_(True),
    ).scalar()
    staff = db.query(func.count(Staff.staff_id)).filter(
        Staff.school_id == school_id,
        Staff.person_type != "teacher",
        Staff.is_active.is_(True),
    ).scalar()
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


# ---- Per-student report ----------------------------------------------------
#
# Assembled server-side on purpose. The marks endpoint returns subject_id with
# no name, and the student endpoint returns class_id with no name, so a client
# doing this itself would have to load every subject and every class to render
# one report, then re-derive the same joins for the next student. Here it is
# one indexed read per section, and the school check happens once.

RECENT_ATTENDANCE_DAYS = 30


def _student_or_none(db: Session, school_id: int, student_id: int):
    """The student, but only if they belong to the caller's school.

    Returning None for a student in another school is what makes this safe:
    the router turns it into the same 404 a genuinely missing student gets, so
    the endpoint cannot be used to probe for which student ids exist elsewhere.
    """
    return (
        db.query(Student)
        .filter(Student.student_id == student_id, Student.school_id == school_id)
        .first()
    )


def _class_name(db: Session, school_id: int, class_id: int) -> str | None:
    if not class_id:
        return None
    return (
        db.query(SchoolClass.name)
        .filter(SchoolClass.class_id == class_id, SchoolClass.school_id == school_id)
        .scalar()
    )


def _guardians(db: Session, school_id: int, student_id: int) -> list[dict]:
    """Every guardian linked to the student, not just the first.

    people_service resolves a student's parent with .first() on parent_id, which
    quietly drops a second guardian. On a report card the guardians are the
    point, so every link is read and the relationship travels with it.
    """
    rows = (
        db.query(Parent, ParentStudent.relationship_)
        .join(ParentStudent, ParentStudent.parent_id == Parent.parent_id)
        .filter(
            ParentStudent.student_id == student_id,
            Parent.school_id == school_id,
        )
        .order_by(Parent.parent_id)
        .all()
    )
    return [
        {
            "parent_id": parent.parent_id,
            "name": parent.name,
            "relationship": relationship,
            "phone": parent.phone,
            "email": parent.email,
            "address": parent.address,
            "emergency_number": parent.emergency_number,
        }
        for parent, relationship in rows
    ]


def _marks_by_term(db: Session, school_id: int, student_id: int) -> list[dict]:
    """Scores grouped by term, with subject names and an average per term.

    Terms are ordered by the data rather than assumed to be "Term 1..3":
    `term` is free text, so a school could be using "Semester 1" and a hardcoded
    ordering would either drop terms or invent one.
    """
    subject_names = dict(
        db.query(Subject.subject_id, Subject.name)
        .filter(Subject.school_id == school_id)
        .all()
    )
    total_subjects = db.query(func.count(Subject.subject_id)).filter(
        Subject.school_id == school_id,
        Subject.is_active.is_(True),
    ).scalar() or 0

    rows = (
        db.query(Mark.term, Mark.subject_id, Mark.score)
        .filter(Mark.student_id == student_id)
        .order_by(Mark.term, Mark.subject_id)
        .all()
    )

    by_term: dict[str, list[dict]] = {}
    for term, subject_id, score in rows:
        by_term.setdefault(term, []).append(
            {
                "subject_id": subject_id,
                # A subject deleted after marks were entered leaves a dangling
                # id. Show the id rather than dropping the score silently.
                "subject_name": subject_names.get(subject_id, f"Subject {subject_id}"),
                "score": score,
            }
        )

    terms = sorted(by_term, key=_term_sort_key)
    out = []
    for term in terms:
        subjects = by_term[term]
        scores = [s["score"] for s in subjects]
        out.append(
            {
                "term": term,
                "subjects": subjects,
                "average": round(sum(scores) / len(scores), 1) if scores else None,
                "graded_subjects": len(subjects),
                "total_subjects": total_subjects,
            }
        )
    return out


def _term_sort_key(term: str):
    """Sort 'Term 2' before 'Term 10', and keep unrecognised labels stable.

    A plain string sort puts "Term 10" before "Term 2" and buries it, and a
    school using "Semester 1" would sort somewhere arbitrary. Extracting the
    first number when there is one handles both, with the label as tiebreak.
    """
    digits = "".join(ch for ch in term if ch.isdigit())
    if digits:
        return (0, int(digits), term)
    return (1, 0, term)


def _attendance(db: Session, student_id: int) -> dict:
    """Counts over the student's whole attendance record, plus recent days.

    There is no term or session concept to bound a report by, and the only
    correct answer to "how often was this student present" is over every row
    that exists. The covered date range is returned so the UI can say which
    period the numbers describe, rather than implying they are current.
    """
    counts = dict(
        db.query(Attendance.status, func.count())
        .filter(Attendance.student_id == student_id)
        .group_by(Attendance.status)
        .all()
    )
    present = counts.get("Present", 0)
    # The column is constrained to Present/Absent, but an older row with any
    # other status must still be counted as a marked day rather than vanish.
    absent = sum(n for status, n in counts.items() if status != "Present")
    marked = present + absent

    span = (
        db.query(func.min(Attendance.date), func.max(Attendance.date))
        .filter(Attendance.student_id == student_id)
        .one()
    )
    first_day, last_day = span

    recent = [
        {"date": str(day), "status": status}
        for day, status in db.query(Attendance.date, Attendance.status)
        .filter(Attendance.student_id == student_id)
        .order_by(Attendance.date.desc())
        .limit(RECENT_ATTENDANCE_DAYS)
        .all()
    ]

    return {
        "present": present,
        "absent": absent,
        "marked_days": marked,
        # None, not 0 and not 100, when nothing was ever marked.
        "percentage": round(present * 100.0 / marked, 1) if marked else None,
        "from_date": str(first_day) if first_day else None,
        "to_date": str(last_day) if last_day else None,
        "recent": recent,
    }


def student_report(db: Session, school_id: int, student_id: int) -> dict | None:
    student = _student_or_none(db, school_id, student_id)
    if student is None:
        return None

    marks_by_term = _marks_by_term(db, school_id, student_id)
    return {
        "student": {
            "student_id": student.student_id,
            "name": student.name,
            "admission_no": student.admission_no,
            "class_id": student.class_id,
            "class_name": _class_name(db, school_id, student.class_id),
            "date_of_birth": str(student.date_of_birth) if student.date_of_birth else None,
            "gender": student.gender,
            "photo_url": student.photo_url,
            "recorded_on": str(student.created_at) if student.created_at else None,
            "guardians": _guardians(db, school_id, student.student_id),
        },
        "terms": [t["term"] for t in marks_by_term],
        "marks_by_term": marks_by_term,
        "attendance": _attendance(db, student.student_id),
    }
