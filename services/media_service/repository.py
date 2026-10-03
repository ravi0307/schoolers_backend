"""Persistence for the school media gallery — a dedicated microservice so
gallery uploads and browsing are owned by one team/contract instead of living
inside the communication service alongside broadcasts."""
from sqlalchemy.orm import Session

from common.dependencies import CurrentUser
from common.exceptions import ForbiddenError, NotFoundError
from common.models import Media, Staff


def resolve_poster_name(db: Session, current_user: CurrentUser) -> str:
    """Derive the gallery poster's display name from the authenticated user
    rather than trusting client-supplied values."""
    # Teachers and staff post under their own name, so an account that isn't
    # linked to a live staff row must not be allowed to upload.
    if current_user.role in ("teacher", "staff"):
        if not current_user.linked_person_id:
            raise ForbiddenError(
                f"This {current_user.role} account isn't linked to a staff record"
            )
        person = (
            db.query(Staff)
            .filter(
                Staff.staff_id == current_user.linked_person_id,
                Staff.is_active.is_(True),
            )
            .first()
        )
        if not person:
            raise ForbiddenError("Staff record not found")
        return person.name

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


def get_media_for_write(db: Session, school_id: int, media_id: int, current_user: CurrentUser) -> Media:
    """Load an active gallery row the caller is allowed to change.

    A school admin may manage anything in their own school (correcting a
    colleague's caption is real work). Staff may only manage media they
    uploaded themselves. Rows with no recorded uploader predate authorship and
    stay admin-only rather than being attributed by name.
    """
    media = (
        db.query(Media)
        .filter(
            Media.media_id == media_id,
            Media.school_id == school_id,
            Media.is_active.is_(True),
        )
        .first()
    )
    # Cross-school and missing rows are both "not found": a teacher who guesses
    # another school's id must not learn that the row exists.
    if not media:
        raise NotFoundError("Media not found")
    if current_user.role != "admin" and media.uploader_user_id != current_user.user_id:
        raise ForbiddenError("You can only edit or remove media you uploaded")
    return media


def update_media(
    db: Session,
    media_id: int,
    media: Media,
    title: str | None = None,
    class_id: int | None = None,
    set_class_id: bool = False,
    file_url: str | None = None,
    media_kind: str | None = None,
) -> Media:
    """Apply an approved edit to a gallery row.

    class_id needs an explicit set_class_id flag because None is a meaningful
    value here (clearing the class), not "leave it alone". Ownership and school
    scope are settled by get_media_for_write before this is called.
    """
    if title is not None:
        media.title = title
    if set_class_id:
        media.class_id = class_id
    if file_url is not None:
        media.file_url = file_url
        media.media_kind = media_kind
    db.commit()
    db.refresh(media)
    return media


def delete_media(db: Session, school_id: int, media_id: int, current_user: CurrentUser) -> Media:
    media = get_media_for_write(db, school_id, media_id, current_user)
    media.is_active = False
    db.commit()
    db.refresh(media)
    return media