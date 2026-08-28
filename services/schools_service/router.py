from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, CurrentUser
from common.exceptions import NotFoundError, ForbiddenError
import repository as repo
from schemas import (
    SchoolCreate, SchoolUpdate, SchoolRead, FeatureFlags, SchoolStatusUpdate, SchoolStats,
)

router = APIRouter(prefix="/schools", tags=["schools"])


def _get_or_404(db: Session, school_id: int):
    school = repo.get_school(db, school_id)
    if not school:
        raise NotFoundError(f"School {school_id} not found")
    return school


def _guard_school_access(current_user: CurrentUser, school_id: int):
    """Master Admin can touch any school; School Admin only their own."""
    if current_user.role == "master":
        return
    if current_user.role == "admin" and current_user.school_id == school_id:
        return
    raise ForbiddenError("Not allowed to access this school")


@router.get("", response_model=list[SchoolRead])
def list_schools(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("master")),
):
    return repo.list_schools(db)


@router.post("", response_model=SchoolRead, status_code=201)
def create_school(
    payload: SchoolCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("master")),
):
    return repo.create_school(db, payload.model_dump())


@router.get("/{school_id}", response_model=SchoolRead)
def get_school(
    school_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("master", "admin")),
):
    _guard_school_access(current_user, school_id)
    return _get_or_404(db, school_id)


@router.patch("/{school_id}", response_model=SchoolRead)
def update_school(
    school_id: int,
    payload: SchoolUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("master")),
):
    school = _get_or_404(db, school_id)
    return repo.update_school(db, school, payload.model_dump(exclude_unset=True))


@router.delete("/{school_id}", status_code=204)
def delete_school(
    school_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("master")),
):
    school = _get_or_404(db, school_id)
    repo.delete_school(db, school)


@router.patch("/{school_id}/features", response_model=SchoolRead)
def update_features(
    school_id: int,
    payload: FeatureFlags,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("master")),
):
    school = _get_or_404(db, school_id)
    return repo.update_school(db, school, payload.model_dump(exclude_unset=True))


@router.patch("/{school_id}/status", response_model=SchoolRead)
def update_status(
    school_id: int,
    payload: SchoolStatusUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("master")),
):
    school = _get_or_404(db, school_id)
    return repo.update_school(db, school, {"status": payload.status})


@router.get("/{school_id}/stats", response_model=SchoolStats)
def get_stats(
    school_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("master", "admin")),
):
    _guard_school_access(current_user, school_id)
    _get_or_404(db, school_id)
    return repo.compute_stats(db, school_id)
