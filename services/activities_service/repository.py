from sqlalchemy.orm import Session

from common.models import Activity


def list_activities(db: Session, school_id: int) -> list[Activity]:
    return db.query(Activity).filter(Activity.school_id == school_id, Activity.is_active.is_(True)).order_by(
        Activity.published_at.desc()
    ).all()


def create_activity(db: Session, school_id: int, data: dict) -> Activity:
    activity = Activity(school_id=school_id, **data)
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity
