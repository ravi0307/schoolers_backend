from datetime import datetime

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from common.models import (
    Broadcast, ParentStudent, Route, RouteStudent, SchoolClass,
    Staff, Student, TeacherClassSubject,
)
from common.exceptions import NotFoundError, ForbiddenError
from common.dependencies import CurrentUser

# Which broadcast audiences each role may address. Identity (role_name and
# sender_name) is always derived server-side, never accepted from the client.
ALLOWED_BROADCAST_SCOPES = {
    "admin": {"school", "class", "route", "pilot"},
    # Staff run the front office and transport desk, so they may address any
    # audience a school admin can -- including routes and pilots.
    "staff": {"school", "class", "route", "pilot"},
    "teacher": {"school", "class"},
    "pilot": {"school", "route"},
}


def create_broadcast(
    db: Session, school_id: int, data: dict, sender_user_id: int | None = None
) -> Broadcast:
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

    # Stamped from the authenticated user rather than accepted from the payload,
    # for the same reason sender_name is: the client must not decide who a
    # broadcast belongs to.
    b = Broadcast(school_id=school_id, sender_user_id=sender_user_id, **data)
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


def _teacher_visible_filter(db: Session, teacher_id: int):
    """Broadcasts a teacher may see: school-wide, the classes they teach, and
    the transport routes carrying students from those classes."""
    taught_class_ids = (
        db.query(TeacherClassSubject.class_id)
        .filter(TeacherClassSubject.staff_id == teacher_id)
    )
    scoped_classes = (
        db.query(SchoolClass.class_id)
        .filter(
            or_(
                SchoolClass.class_id.in_(taught_class_ids),
                SchoolClass.class_teacher_staff_id == teacher_id,
            )
        )
    )
    scoped_routes = (
        db.query(RouteStudent.route_id)
        .join(Student, Student.student_id == RouteStudent.student_id)
        .filter(Student.class_id.in_(scoped_classes))
    )
    return or_(
        Broadcast.scope == "school",
        and_(Broadcast.scope == "class", Broadcast.class_id.in_(scoped_classes)),
        and_(Broadcast.scope == "route", Broadcast.route_id.in_(scoped_routes)),
    )


def resolve_sender_identity(db: Session, current_user: CurrentUser) -> tuple[str, str]:
    """Derive the broadcast's (role_name, sender_name) from the authenticated
    user rather than trusting client-supplied values, so a teacher or pilot
    cannot impersonate an admin."""
    role = current_user.role

    def linked_staff() -> Staff | None:
        return (
            db.query(Staff)
            .filter(
                Staff.staff_id == current_user.linked_person_id,
                Staff.is_active.is_(True),
            )
            .first()
        )

    # Teachers, pilots and staff broadcast under their own name, so an account
    # that isn't linked to a live staff row must not be allowed to post.
    if role in ("teacher", "pilot", "staff"):
        if not current_user.linked_person_id:
            raise ForbiddenError(
                f"This {role} account isn't linked to a staff record"
            )
        staff = linked_staff()
        if not staff:
            raise ForbiddenError("Staff record not found")
        return {"teacher": "Teacher", "pilot": "Pilot", "staff": "Staff"}[role], staff.name

    if current_user.linked_person_id:
        staff = linked_staff()
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
    if role in ("admin", "staff"):
        # School admins and staff see every broadcast in their school. Staff run
        # the front desk, so they need the whole feed rather than a scoped slice.
        pass
    elif role == "teacher":
        if current_user.linked_person_id is not None:
            q = q.filter(_teacher_visible_filter(db, current_user.linked_person_id))
        else:
            # An unlinked teacher has no assigned classes to scope by — only
            # school-wide announcements are safe to show.
            q = q.filter(Broadcast.scope == "school")
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
    created_at: datetime | None,
    current_user: CurrentUser,
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
    # An admin may correct anyone's typo; everyone else -- staff included -- may
    # only rewrite a broadcast they posted themselves. Authorship is the user id
    # recorded at creation, never the display name, because two staff can share
    # the same derived label.
    if current_user.role != "admin" and broadcast.sender_user_id != current_user.user_id:
        raise ForbiddenError("You can only edit broadcasts you posted")
    broadcast.message = message
    if created_at is not None:
        broadcast.created_at = created_at
    db.commit()
    db.refresh(broadcast)
    return broadcast


def delete_broadcast(
    db: Session, school_id: int, broadcast_id: int, current_user: CurrentUser
) -> Broadcast:
    """Soft-delete a broadcast after checking tenant and authorship."""
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
    if current_user.role != "admin" and broadcast.sender_user_id != current_user.user_id:
        raise ForbiddenError("You can only delete broadcasts you posted")
    broadcast.is_active = False
    db.commit()
    db.refresh(broadcast)
    return broadcast
