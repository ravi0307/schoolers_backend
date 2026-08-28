from sqlalchemy.orm import Session

from common.models import SchoolNotification


def list_for_school(db: Session, school_id: int) -> list[SchoolNotification]:
    return db.query(SchoolNotification).filter(
        SchoolNotification.school_id == school_id
    ).order_by(SchoolNotification.sent_at.desc()).all()


def create(db: Session, school_id: int, data: dict) -> SchoolNotification:
    n = SchoolNotification(school_id=school_id, **data)
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


def mark_read(db: Session, notification_id: int) -> SchoolNotification | None:
    n = db.query(SchoolNotification).filter(SchoolNotification.notification_id == notification_id).first()
    if n:
        n.status = "Read"
        db.commit()
        db.refresh(n)
    return n
