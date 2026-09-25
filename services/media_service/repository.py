"""Persistence for the school media gallery — a dedicated microservice so
gallery uploads and browsing are owned by one team/contract instead of living
inside the communication service alongside broadcasts."""
from sqlalchemy.orm import Session

from common.dependencies import CurrentUser
from common.exceptions import ForbiddenError, NotFoundError
from common.models import Media, Staff, Teacher


def resolve_poster_name(db: Session, current_user: CurrentUser) -> str:
    """Derive the gallery poster's display name from the authenticated user
    rather than trusting client-supplied values."""
    if current_user.role == "teacher":
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
        return teacher.name

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
            return staff.name
    return "School Admin"


def create_media(db: Session, school_id: int, data: dict) -> Media:
    media = Media(school_id=school_id, **data)
    db.add(media)
    db.commit()
    db.refresh(media)
    return media


def list_media(db: Session, school_id: int, class_id: int | None = None) -> list[Media]:
    q = db.query(Media).filter(Media.school_id == school_id, Media.is_active.is_(True))
    if class_id:
        q = q.filter(Media.class_id == class_id)
    return q.order_by(Media.created_at.desc()).all()


def delete_media(db: Session, school_id: int, media_id: int) -> Media:
    media = (
        db.query(Media)
        .filter(
            Media.media_id == media_id,
            Media.school_id == school_id,
            Media.is_active.is_(True),
        )
        .first()
    )
    if not media:
        raise NotFoundError("Media not found")
    media.is_active = False
    db.commit()
    db.refresh(media)
    return media