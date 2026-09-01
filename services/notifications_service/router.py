from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, CurrentUser
from common.exceptions import NotFoundError
import repository as repo
from schemas import NotificationCreate, NotificationRead

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/school/{school_id}", response_model=list[NotificationRead])
def list_for_school(
    school_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin", "master")),
):
    if current_user.role == "admin" and current_user.school_id != school_id:
        return []
    return [NotificationRead.from_notification(n) for n in repo.list_for_school(db, school_id)]


@router.post("/school/{school_id}", response_model=NotificationRead, status_code=201)
def send_notification(
    school_id: int,
    payload: NotificationCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("master")),
):
    notification = repo.create(db, school_id, payload.model_dump())
    return NotificationRead.from_notification(notification)


@router.patch("/{notification_id}/read", response_model=NotificationRead)
def mark_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    n = repo.mark_read(db, notification_id)
    if not n:
        raise NotFoundError("Notification not found")
    return NotificationRead.from_notification(n)
