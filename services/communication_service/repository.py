from datetime import datetime

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from common.models import (
    Broadcast, Media, ParentStudent, Pilot, Route, RouteStudent, SchoolClass,
    Staff, Student, Teacher,
)
from common.exceptions import NotFoundError, ForbiddenError
from common.dependencies import CurrentUser

# Which broadcast audiences each role may address. Identity (role_name and
# sender_name) is always derived server-side, never accepted from the client.
ALLOWED_BROADCAST_SCOPES = {
    "admin": {"school", "class", "route", "pilot"},
    "teacher": {"school", "class"},
    "pilot": {"school", "route"},
}


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


def resolve_sender_identity(db: Session, current_user: CurrentUser) -> tuple[str, str]:
    """Derive the broadcast's (role_name, sender_name) from the authenticated
    user rather than trusting client-supplied values, so a teacher or pilot
    cannot impersonate an admin."""
    role = current_user.role
    if role == "teacher":
        if not current_user.linked_person_id:
            raise ForbiddenError("This teacher account isn't linked to a teacher record")
        teacher = (
            db.query(Teacher)
            .filter(
                Teacher.teacher_id == current_user.linked_person_id,
                Teacher.is_active.is_(True),
            )
            .first()
        )
        if not teacher:
            raise ForbiddenError("Teacher record not found")
        return "Teacher", teacher.name
    if role == "pilot":
        pilot = (
            db.query(Pilot)
            .filter(Pilot.user_id == current_user.user_id)
            .first()
        )
        if not pilot:
            raise ForbiddenError("Pilot record not found")
        return "Pilot", pilot.full_name
    if current_user.linked_person_id:
        staff = (
            db.query(Staff)
            .filter(
                Staff.staff_id == current_user.linked_person_id,
                Staff.is_active.is_(True),
            )
            .first()
        )
        if staff:
            return "Admin", staff.name
    return "Admin", "Admin"


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
    elif role == "parent":
        if current_user.linked_person_id is not None:
            q = q.filter(_parent_visible_filter(db, current_user.linked_person_id))
        else:
            # An unlinked parent has no children to scope by — only school-wide
            # announcements are safe to show, never class/route/pilot feeds.
            q = q.filter(Broadcast.scope == "school")

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