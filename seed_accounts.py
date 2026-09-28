"""Seed the accounts grid: a flat monthly salary per staff member and fee per
student, across the six-month window the admin view shows (idempotent). Each
payment is stamped with a payday -- the 28th of its month -- so the grid's
"paid on" dates are not blank in a fresh demo.

Written as a script rather than raw SQL so the month window comes from the same
`recent_months` the API uses. If the two ever disagreed, the grid would show
columns the seed never filled.

Existing rows are left alone rather than overwritten: a real school's accounts
must not be clobbered by re-running a demo seed. Pass --force to overwrite.
"""
import argparse
import os
from datetime import date

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://ravi@localhost:5432/schoolersdb?options=-csearch_path%3Dschoolers",
)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from common.models import Staff, StaffSalary, Student, StudentFee
import services.accounts_service.repository as accounts_repo

DEFAULT_SALARY = 10000.00
DEFAULT_FEE = 4000.00


def _paid_on(month: str) -> date:
    """A believable payday for a month bucket: the 28th of that month."""
    return date(int(month[:4]), int(month[5:7]), 28)


def seed(engine, salary=DEFAULT_SALARY, fee=DEFAULT_FEE, force=False, months=6):
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    db = Session()
    try:
        window = accounts_repo.recent_months(months)
        salaries = fees = skipped = 0

        staff = (
            db.query(Staff)
            .filter(Staff.is_active.is_(True))
            .order_by(Staff.school_id, Staff.staff_id)
            .all()
        )
        for person in staff:
            for month in window:
                exists = (
                    db.query(StaffSalary)
                    .filter(StaffSalary.staff_id == person.staff_id, StaffSalary.month == month)
                    .first()
                )
                if exists:
                    if not force:
                        skipped += 1
                        continue
                    exists.amount = salary
                    exists.paid_on = _paid_on(month)
                    salaries += 1
                    continue
                db.add(
                    StaffSalary(
                        school_id=person.school_id,
                        staff_id=person.staff_id,
                        month=month,
                        amount=salary,
                        paid_on=_paid_on(month),
                    )
                )
                salaries += 1

        students = (
            db.query(Student)
            .filter(Student.is_active.is_(True))
            .order_by(Student.school_id, Student.student_id)
            .all()
        )
        for person in students:
            for month in window:
                exists = (
                    db.query(StudentFee)
                    .filter(StudentFee.student_id == person.student_id, StudentFee.month == month)
                    .first()
                )
                if exists:
                    if not force:
                        skipped += 1
                        continue
                    exists.amount = fee
                    exists.paid_on = _paid_on(month)
                    fees += 1
                    continue
                db.add(
                    StudentFee(
                        school_id=person.school_id,
                        student_id=person.student_id,
                        month=month,
                        amount=fee,
                        paid_on=_paid_on(month),
                    )
                )
                fees += 1

        db.commit()
        return window, salaries, fees, skipped
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--salary", type=float, default=DEFAULT_SALARY)
    ap.add_argument("--fee", type=float, default=DEFAULT_FEE)
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing amounts instead of leaving them alone",
    )
    args = ap.parse_args()

    from common.config import settings

    eng = create_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)
    window, salaries, fees, skipped = seed(
        eng, args.salary, args.fee, args.force, args.months
    )
    print(f"window: {window[0]} .. {window[-1]} ({len(window)} months)")
    print(f"salaries written: {salaries}, fees written: {fees}, left alone: {skipped}")
