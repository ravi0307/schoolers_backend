from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
import repository as repo
from schemas import (
    BroadcastCreate, BroadcastRead, BroadcastUpdate, MediaCreate, MediaRead,
)

router = APIRouter(tags=["communication"])


@router.post("/broadcasts", response_model=BroadcastRead, status_code=201)
def create_broadcast(
    payload: BroadcastCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("teacher", "admin", "pilot")),
):
    return repo.create_broadcast(db, school_id, payload.model_dump())


@router.get("/broadcasts", response_model=list[BroadcastRead])
def list_broadcasts(
    scope: str | None = Query(default=None),
    class_id: int | None = Query(default=None),
    route_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin", "pilot")),
):
    return repo.list_broadcasts(db, school_id, current_user, scope, class_id, route_id)


@router.patch("/broadcasts/{broadcast_id}", response_model=BroadcastRead)
def update_broadcast(
    broadcast_id: int,
    payload: BroadcastUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("teacher", "admin", "pilot")),
):
    return repo.update_broadcast_message(
        db,
        school_id,
        broadcast_id,
        payload.message,
        payload.created_at,
    )


@router.post("/media", response_model=MediaRead, status_code=201)
def create_media(
    payload: MediaCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    return repo.create_media(db, school_id, payload.model_dump())


@router.get("/media", response_model=list[MediaRead])
def list_media(
    class_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin")),
):
    return repo.list_media(db, school_id, class_id)
