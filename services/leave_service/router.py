from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
from common.exceptions import NotFoundError
import repository as repo
from schemas import LeaveRequestCreate, LeaveRequestRead

router = APIRouter(prefix="/leave", tags=["leave"])


@router.post("", response_model=LeaveRequestRead, status_code=201)
def create_leave_request(
    payload: LeaveRequestCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "staff", "pilot", "admin")),
):
    return repo.create(db, school_id, payload.model_dump())


@router.get("", response_model=list[LeaveRequestRead])
def list_leave_requests(
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.list_for_school(db, school_id, status)


@router.get("/mine", response_model=list[LeaveRequestRead])
def list_my_children_leave_requests(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent")),
):
    """Leave requests filed for the logged-in parent's children."""
    names = repo.child_names_of_parent(db, school_id, current_user.linked_person_id)
    return repo.list_for_child_names(db, school_id, names)


@router.patch("/{leave_id}/approve", response_model=LeaveRequestRead)
def approve(
    leave_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    leave = repo.get(db, leave_id)
    if not leave:
        raise NotFoundError("Leave request not found")
    return repo.set_status(db, leave, "Approved")


@router.patch("/{leave_id}/reject", response_model=LeaveRequestRead)
def reject(
    leave_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    leave = repo.get(db, leave_id)
    if not leave:
        raise NotFoundError("Leave request not found")
    return repo.set_status(db, leave, "Rejected")
