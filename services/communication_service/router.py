from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
from common.exceptions import ForbiddenError
import repository as repo
from schemas import (
    BroadcastCreate, BroadcastRead, BroadcastUpdate,
)

router = APIRouter(tags=["communication"])


@router.post("/broadcasts", response_model=BroadcastRead, status_code=201)
def create_broadcast(
    payload: BroadcastCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("teacher", "admin", "pilot")),
):
    if payload.scope not in repo.ALLOWED_BROADCAST_SCOPES.get(current_user.role, set()):
        raise ForbiddenError(
            f"Role '{current_user.role}' cannot create a '{payload.scope}' broadcast"
        )
    role_name, sender_name = repo.resolve_sender_identity(db, current_user)
    data = payload.model_dump()
    data["role_name"] = role_name
    data["sender_name"] = sender_name
    return repo.create_broadcast(db, school_id, data)


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
    current_user: CurrentUser = Depends(require_role("admin")),
):
    # Only a school admin may edit a broadcast — there is no per-broadcast
    # creator column, so teachers/pilots must not be able to rewrite messages
    # they didn't author.
    return repo.update_broadcast_message(
        db,
        school_id,
        broadcast_id,
        payload.message,
        payload.created_at,
    )
