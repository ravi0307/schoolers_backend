from sqlalchemy.orm import Session

from common.models import Broadcast, Media


def create_broadcast(db: Session, school_id: int, data: dict) -> Broadcast:
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
