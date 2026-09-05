from datetime import datetime

from sqlalchemy.orm import Session

from common.models import Broadcast, Media, SchoolClass
from common.exceptions import NotFoundError


def create_broadcast(db: Session, school_id: int, data: dict) -> Broadcast:
    class_id = data.get("class_id")
    if class_id is not None:
        target_class = (
            db.query(SchoolClass)
            .filter(
                SchoolClass.class_id == class_id,
                SchoolClass.school_id == school_id,
                SchoolClass.is_active.is_(True),
            )
            .first()
        )
        if not target_class:
            raise NotFoundError("Class not found for this school")

    b = Broadcast(school_id=school_id, **data)
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def list_broadcasts(db: Session, school_id: int, scope: str | None, class_id: int | None) -> list[Broadcast]:
    q = db.query(Broadcast).filter(Broadcast.school_id == school_id, Broadcast.is_active.is_(True))
    if scope:
        q = q.filter(Broadcast.scope == scope)
    if class_id:
        q = q.filter(Broadcast.class_id == class_id)
    return q.order_by(Broadcast.created_at.desc()).all()


def update_broadcast_message(
    db: Session,
    school_id: int,
    broadcast_id: int,
    message: str,
    created_at: datetime | None = None,
) -> Broadcast:
    broadcast = (
        db.query(Broadcast)
        .filter(
            Broadcast.broadcast_id == broadcast_id,
            Broadcast.school_id == school_id,
            Broadcast.is_active.is_(True),
        )
        .first()
    )
    if not broadcast:
        raise NotFoundError("Broadcast not found")
    broadcast.message = message
    if created_at is not None:
        broadcast.created_at = created_at
    db.commit()
    db.refresh(broadcast)
    return broadcast


def create_media(db: Session, school_id: int, data: dict) -> Media:
    m = Media(school_id=school_id, **data)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def list_media(db: Session, school_id: int, class_id: int | None) -> list[Media]:
    q = db.query(Media).filter(Media.school_id == school_id, Media.is_active.is_(True))
    if class_id:
        q = q.filter(Media.class_id == class_id)
    return q.order_by(Media.created_at.desc()).all()
