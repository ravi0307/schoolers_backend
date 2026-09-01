import logging

from sqlalchemy.orm import Session

from common.email import is_email_configured, school_recipients, send_school_notification_email
from common.exceptions import AppError, NotFoundError
from common.models import School, SchoolNotification

logger = logging.getLogger(__name__)


def list_for_school(db: Session, school_id: int) -> list[SchoolNotification]:
    return db.query(SchoolNotification).filter(
        SchoolNotification.school_id == school_id
    ).order_by(SchoolNotification.sent_at.desc()).all()


def create(db: Session, school_id: int, data: dict) -> SchoolNotification:
    school = db.query(School).filter(
        School.school_id == school_id,
        School.is_active.is_(True),
    ).first()
    if not school:
        raise NotFoundError("School not found")

    recipients = school_recipients(school)
    if not recipients:
        raise AppError("School has no email address on file")

    if not is_email_configured():
        raise AppError(
            "Email is not configured on the server. Set SMTP_HOST and SMTP_FROM in common/.env"
        )

    n = SchoolNotification(school_id=school_id, **data)
    db.add(n)
    db.commit()
    db.refresh(n)

    try:
        send_school_notification_email(school.name, data["type"], data["message"], recipients)
    except Exception as exc:
        logger.exception("Failed to email school notification %s", n.notification_id)
        n.status = "Email failed"
        db.commit()
        db.refresh(n)
        raise AppError(f"Notification saved but email could not be sent: {exc}") from exc

    n._email_recipients = recipients
    return n


def mark_read(db: Session, notification_id: int) -> SchoolNotification | None:
    n = db.query(SchoolNotification).filter(SchoolNotification.notification_id == notification_id).first()
    if n:
        n.status = "Read"
        db.commit()
        db.refresh(n)
    return n
