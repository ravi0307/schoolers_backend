from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
import repository as repo
from schemas import ActivityCreate, ActivityRead

router = APIRouter(prefix="/activities", tags=["activities"])


@router.get("", response_model=list[ActivityRead])
def list_activities(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "admin")),
):
    return repo.list_activities(db, school_id)


@router.post("", response_model=ActivityRead, status_code=201)
def create_activity(
    payload: ActivityCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.create_activity(db, school_id, payload.model_dump())
