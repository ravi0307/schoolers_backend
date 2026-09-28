from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
from common.exceptions import ForbiddenError, NotFoundError
import repository as repo
from schemas import SchoolOverview, ClassAttendanceTrendPoint, StudentReport

router = APIRouter(prefix="/reports", tags=["reports"])


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
