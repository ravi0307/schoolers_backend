from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
from common.exceptions import ForbiddenError, NotFoundError
import repository as repo
from schemas import (
    SchoolOverview,
    ClassAttendanceTrendPoint,
    StudentReport,
    StaffReport,
    StaffSelfSummary,
)

router = APIRouter(prefix="/reports", tags=["reports"])

# Roles whose account links to a Staff row, so a "my own" report exists for
# them. Parent and master are absent on purpose: a parent links to a Parent row
# and a master to nothing, so neither has attendance or salary to read.
SELF_ROLES = ("admin", "teacher", "staff", "pilot")


@router.get("/school/{school_id}/overview", response_model=SchoolOverview)
def school_overview(
    school_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin", "master")),
):
    if current_user.role == "admin" and current_user.school_id != school_id:
        raise ForbiddenError("Not allowed to view another school's report")
    return repo.school_overview(db, school_id)


@router.get("/class/{class_id}/attendance-trend", response_model=list[ClassAttendanceTrendPoint])
def class_attendance_trend(
    class_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin", "teacher")),
):
    return repo.class_attendance_trend(db, class_id)


# The router already carries prefix="/reports", so the path must not repeat it
# or the endpoint mounts at /api/v1/reports/reports/student/...
@router.get("/student/{student_id}", response_model=StudentReport)
def student_report(
    student_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    """One student's details, marks and attendance, in a single read.

    Admin only, and scoped to the caller's own school. A student belonging to
    another school raises the same NotFound as one who does not exist, so this
    cannot be used to discover which student ids another school holds.
    """
    report = repo.student_report(db, school_id, student_id)
    if report is None:
        raise NotFoundError("Student not found")
    return report


@router.get("/staff/me", response_model=StaffSelfSummary)
def my_staff_summary(
    months: int = Query(repo.DEFAULT_SALARY_MONTHS, ge=1, le=24),
    salary_end: str | None = Query(
        None,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description="Anchor month for the salary window, YYYY-MM. Defaults to the current month.",
    ),
    attendance_month: str | None = Query(
        None,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description="Narrow the attendance history to one month, YYYY-MM. Omit for the whole register.",
    ),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(*SELF_ROLES)),
):
    """The caller's own salary and attendance, for their profile page.

    There is no staff id in the path or the query, so this cannot be pointed at
    anyone else: the row is resolved from current_user.linked_person_id, the
    same scoping the staff attendance endpoint already applies. GET
    /reports/staff/{staff_id} stays admin-only for reading other people.

    An account with no linked staff row is refused rather than answered with an
    empty summary, since a blank panel would read as "you have no attendance"
    when the truth is that there is no staff record to read.
    """
    if current_user.school_id is None:
        raise ForbiddenError("No school is associated with this account")
    if not current_user.linked_person_id:
        raise ForbiddenError("This account isn't linked to a staff record")

    summary = repo.staff_self_summary(
        db, current_user.school_id, current_user.linked_person_id,
        months, salary_end, attendance_month,
    )
    if summary is None:
        # The session points at a staff row that is not in this school, which
        # means the record moved. Answering 403 keeps it as "not yours" rather
        # than a 404 that invites probing for ids.
        raise ForbiddenError("This account isn't linked to a staff record")
    return summary


@router.get("/staff/{staff_id}", response_model=StaffReport)
def staff_report(
    staff_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    """One staff member's details, salary and attendance, in a single read.

    Admin only, and scoped to the caller's own school. A staff member of
    another school raises the same 404 as one who does not exist, matching the
    student report so foreign ids stay un-discoverable.
    """
    report = repo.staff_report(db, school_id, staff_id)
    if report is None:
        raise NotFoundError("Staff not found")
    return report
