from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, CurrentUser
from common.exceptions import ForbiddenError
import repository as repo
from schemas import SchoolOverview, ClassAttendanceTrendPoint

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
