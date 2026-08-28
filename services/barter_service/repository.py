from sqlalchemy.orm import Session

from common.models import BarterListing


def list_listings(db: Session, school_id: int) -> list[BarterListing]:
    return db.query(BarterListing).filter(BarterListing.school_id == school_id, BarterListing.is_active.is_(True)).order_by(
        BarterListing.created_at.desc()
    ).all()


def create_listing(db: Session, school_id: int, data: dict) -> BarterListing:
    listing = BarterListing(school_id=school_id, **data)
    db.add(listing)
    db.commit()
    db.refresh(listing)
    return listing


def get_listing(db: Session, school_id: int, listing_id: int) -> BarterListing | None:
    return db.query(BarterListing).filter(
        BarterListing.school_id == school_id, BarterListing.listing_id == listing_id, BarterListing.is_active.is_(True)
    ).first()


def update_listing(db: Session, listing: BarterListing, data: dict) -> BarterListing:
    for k, v in data.items():
        if v is not None:
            setattr(listing, k, v)
    db.commit()
    db.refresh(listing)
    return listing


def delete_listing(db: Session, listing: BarterListing) -> None:
    listing.is_active = False
    db.commit()
