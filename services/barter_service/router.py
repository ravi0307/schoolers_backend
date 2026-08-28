from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
from common.exceptions import NotFoundError
import repository as repo
from schemas import BarterCreate, BarterUpdate, BarterRead

router = APIRouter(prefix="/barter", tags=["barter"])


@router.get("", response_model=list[BarterRead])
def list_listings(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent")),
):
    return repo.list_listings(db, school_id)


@router.post("", response_model=BarterRead, status_code=201)
def create_listing(
    payload: BarterCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent")),
):
    return repo.create_listing(db, school_id, payload.model_dump())


@router.patch("/{listing_id}", response_model=BarterRead)
def update_listing(
    listing_id: int,
    payload: BarterUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent")),
):
    listing = repo.get_listing(db, school_id, listing_id)
    if not listing:
        raise NotFoundError("Listing not found")
    return repo.update_listing(db, listing, payload.model_dump(exclude_unset=True))


@router.delete("/{listing_id}", status_code=204)
def delete_listing(
    listing_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent")),
):
    listing = repo.get_listing(db, school_id, listing_id)
    if not listing:
        raise NotFoundError("Listing not found")
    repo.delete_listing(db, listing)
