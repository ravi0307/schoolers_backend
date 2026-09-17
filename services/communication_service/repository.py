from datetime import datetime

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from common.models import Broadcast, Media, ParentStudent, Route, RouteStudent, SchoolClass, Student
from common.exceptions import NotFoundError
from common.dependencies import CurrentUser


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

    route_id = data.get("route_id")
    if route_id is not None:
        target_route = (
            db.query(Route)
            .filter(
                Route.route_id == route_id,
                Route.school_id == school_id,
                Route.is_active.is_(True),
            )
            .first()
        )
        if not target_route:
            raise NotFoundError("Route not found for this school")

    b = Broadcast(school_id=school_id, **data)
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def _parent_visible_filter(db: Session, parent_id: int):
    """Broadcasts a parent may see: school-wide, their children's classes,
    and the transport routes their children ride."""
    child_classes = (
        db.query(Student.class_id)
        .join(ParentStudent, ParentStudent.student_id == Student.student_id)
        .filter(ParentStudent.parent_id == parent_id)
    )
    child_routes = (
        db.query(RouteStudent.route_id)
        .join(Student, Student.student_id == RouteStudent.student_id)
        .join(ParentStudent, ParentStudent.student_id == Student.student_id)
        .filter(ParentStudent.parent_id == parent_id)
    )
    return or_(
        Broadcast.scope == "school",
        and_(Broadcast.scope == "class", Broadcast.class_id.in_(child_classes)),
        and_(Broadcast.scope == "route", Broadcast.route_id.in_(child_routes)),
    )


def list_broadcasts(
    db: Session,
    school_id: int,
    current_user: CurrentUser,
    scope: str | None = None,
    class_id: int | None = None,
    route_id: int | None = None,
) -> list[Broadcast]:
    q = db.query(Broadcast).filter(Broadcast.school_id == school_id, Broadcast.is_active.is_(True))

    role = current_user.role
    if role == "admin":
        pass  # school admins see every broadcast in their school.
    elif role == "teacher":
        q = q.filter(Broadcast.scope.in_(["school", "class", "route"]))
    elif role == "pilot":
        q = q.filter(Broadcast.scope.in_(["school", "route", "pilot"]))
    elif role == "parent" and current_user.linked_person_id is not None:
        q = q.filter(_parent_visible_filter(db, current_user.linked_person_id))

    if scope:
        q = q.filter(Broadcast.scope == scope)
    if class_id:
        q = q.filter(Broadcast.class_id == class_id)
    if route_id:
        q = q.filter(Broadcast.route_id == route_id)
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