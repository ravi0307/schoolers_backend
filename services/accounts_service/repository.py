"""Accounts: staff salaries and student fees, read as a month grid.

Both sheets are "every active person of the school x the last N months", so
they are built as a LEFT JOIN from the person to their payments. People with
no payments in the window still appear, with an empty `amounts` map -- the
admin needs to see who has not been paid, which is the whole point of the
page, so an inner join would hide exactly the rows that matter.
"""
from datetime import date

from sqlalchemy.orm import Session

from common.exceptions import NotFoundError
from common.models import SchoolClass, Staff, StaffSalary, Student, StudentFee

DEFAULT_MONTHS = 6


def month_window(count: int = DEFAULT_MONTHS, end: str | None = None) -> list[str]:
    """`count` calendar months ending at `end` ('YYYY-MM'), oldest first.

    `end` defaults to the current month, which is what the admin grid shows on
    first load. Passing it explicitly is what lets the month selector page
    backwards: without an anchor the API could only ever answer "the last six
    months", and there would be no previous page to show.

    The window is computed here rather than in each caller so the API, the
    seed script and the grid columns can never disagree about which six months
    "the last six months" means.
    """
    if end:
        year, month = int(end[:4]), int(end[5:7])
    else:
        today = date.today()
        year, month = today.year, today.month

    months = []
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(months))


def recent_months(count: int = DEFAULT_MONTHS, today: date | None = None) -> list[str]:
    """The last `count` calendar months ending today, oldest first.

    For 2026-09 with count=6 this is 2026-04 .. 2026-09. Kept as the unanchored
    form the seed script and most tests want; month_window is the general case.
    """
    if today is None:
        return month_window(count)
    return month_window(count, f"{today.year:04d}-{today.month:02d}")


def _amount(value) -> float:
    return float(value) if value is not None else 0.0


def salary_sheet(db: Session, school_id: int, months: list[str]) -> dict:
    staff = (
        db.query(Staff)
        .filter(Staff.school_id == school_id, Staff.is_active.is_(True))
        .order_by(Staff.name)
        .all()
    )
    payments = (
        db.query(StaffSalary)
        .filter(StaffSalary.school_id == school_id, StaffSalary.month.in_(months))
        .all()
    )
    by_staff: dict[int, dict[str, float]] = {}
    for pay in payments:
        by_staff.setdefault(pay.staff_id, {})[pay.month] = _amount(pay.amount)

    rows = [
        {
            "staff_id": s.staff_id,
            "staff_name": s.name,
            "designation": s.role_title or s.role,
            "amounts": by_staff.get(s.staff_id, {}),
        }
        for s in staff
    ]
    return {
        "months": months,
        "rows": rows,
        "total_paid": sum(sum(r["amounts"].values()) for r in rows),
        # A month is outstanding for a person when nothing was recorded for it.
        "total_outstanding_months": sum(
            len(months) - len(r["amounts"]) for r in rows
        ),
    }


def fee_sheet(db: Session, school_id: int, months: list[str]) -> dict:
    students = (
        db.query(Student, SchoolClass)
        .outerjoin(SchoolClass, SchoolClass.class_id == Student.class_id)
        .filter(Student.school_id == school_id, Student.is_active.is_(True))
        .order_by(Student.name)
        .all()
    )
    payments = (
        db.query(StudentFee)
        .filter(StudentFee.school_id == school_id, StudentFee.month.in_(months))
        .all()
    )
    by_student: dict[int, dict[str, float]] = {}
    for pay in payments:
        by_student.setdefault(pay.student_id, {})[pay.month] = _amount(pay.amount)

    rows = [
        {
            "student_id": s.student_id,
            "student_name": s.name,
            "admission_no": s.admission_no,
            "class_name": klass.name if klass else None,
            "amounts": by_student.get(s.student_id, {}),
        }
        for s, klass in students
    ]
    return {
        "months": months,
        "rows": rows,
        "total_collected": sum(sum(r["amounts"].values()) for r in rows),
        "outstanding_count": sum(
            1 for r in rows if len(r["amounts"]) < len(months)
        ),
    }


def _owned_staff(db: Session, school_id: int, staff_id: int) -> Staff:
    staff = (
        db.query(Staff)
        .filter(
            Staff.staff_id == staff_id,
            Staff.school_id == school_id,
            Staff.is_active.is_(True),
        )
        .first()
    )
    if not staff:
        # 404 rather than 403: do not confirm that another school's staff
        # record exists. Same reasoning as the transport scoping fix.
        # Inactive staff are rejected for the same reason -- the salary sheet
        # only lists active staff, so a payment against one would be recorded
        # but never shown, which is worse than refusing it.
        raise NotFoundError("Staff not found")
    return staff


def _owned_student(db: Session, school_id: int, student_id: int) -> Student:
    student = (
        db.query(Student)
        .filter(Student.student_id == student_id, Student.school_id == school_id)
        .first()
    )
    if not student:
        raise NotFoundError("Student not found")
    return student


def record_salary(db: Session, school_id: int, data: dict) -> dict:
    """Record (or overwrite) one staff member's salary for one month.

    The unique constraint is on (staff_id, month), so a second payment for the
    same month replaces the first rather than adding a row -- one figure per
    grid cell. school_id is re-asserted on write so a row can never end up
    holding a school that disagrees with the person it pays.
    """
    staff = _owned_staff(db, school_id, data["staff_id"])
    existing = (
        db.query(StaffSalary)
        .filter(StaffSalary.staff_id == staff.staff_id, StaffSalary.month == data["month"])
        .first()
    )
    if existing:
        existing.amount = data["amount"]
        existing.paid_on = data.get("paid_on")
        existing.note = data.get("note")
        existing.school_id = school_id
        row = existing
    else:
        row = StaffSalary(
            school_id=school_id,
            staff_id=staff.staff_id,
            month=data["month"],
            amount=data["amount"],
            paid_on=data.get("paid_on"),
            note=data.get("note"),
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "staff_id": row.staff_id,
        "month": row.month,
        "amount": _amount(row.amount),
        "paid_on": row.paid_on,
        "note": row.note,
    }


def record_fee(db: Session, school_id: int, data: dict) -> dict:
    """Record (or overwrite) one student's fee for one month."""
    student = _owned_student(db, school_id, data["student_id"])
    existing = (
        db.query(StudentFee)
        .filter(
            StudentFee.student_id == student.student_id,
            StudentFee.month == data["month"],
        )
        .first()
    )
    if existing:
        existing.amount = data["amount"]
        existing.paid_on = data.get("paid_on")
        existing.note = data.get("note")
        existing.school_id = school_id
        row = existing
    else:
        row = StudentFee(
            school_id=school_id,
            student_id=student.student_id,
            month=data["month"],
            amount=data["amount"],
            paid_on=data.get("paid_on"),
            note=data.get("note"),
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "student_id": row.student_id,
        "month": row.month,
        "amount": _amount(row.amount),
        "paid_on": row.paid_on,
        "note": row.note,
    }


def delete_salary(db: Session, school_id: int, staff_id: int, month: str) -> None:
    row = (
        db.query(StaffSalary)
        .filter(
            StaffSalary.school_id == school_id,
            StaffSalary.staff_id == staff_id,
            StaffSalary.month == month,
        )
        .first()
    )
    if row:
        db.delete(row)
        db.commit()


def delete_fee(db: Session, school_id: int, student_id: int, month: str) -> None:
    row = (
        db.query(StudentFee)
        .filter(
            StudentFee.school_id == school_id,
            StudentFee.student_id == student_id,
            StudentFee.month == month,
        )
        .first()
    )
    if row:
        db.delete(row)
        db.commit()
