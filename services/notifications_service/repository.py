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

    delivered = send_school_notification_email(
        school.name,
        data["type"],
        data["message"],
        recipients,
        db=db,
        school_email=school.primary_email,
        school_phone=school.primary_contact,
    )
    if not delivered:
        # The row is already committed and shows in the school's notification
        # history. Do NOT rewrite status here: the column is varchar(10)
        # CHECK (Sent, Read), so any failure marker crashes on the way out
        # and masks this error with a 500. The outbox keeps an explicit
        # Failed delivery row for audit and bounded retry; the client only
        # sees a safe message — SMTP details stay in the server logs.
        logger.warning(
            "Notification %s saved but email delivery failed for school %s",
            n.notification_id, school_id,
        )
        raise AppError(
            "Notification saved to the school's notification history, but the "
            "email could not be sent. The delivery will be retried "
            "automatically; no need to resend it."
        )

    n._email_recipients = recipients
    return n


def mark_read(db: Session, notification_id: int) -> SchoolNotification | None:
    n = db.query(SchoolNotification).filter(SchoolNotification.notification_id == notification_id).first()
    if n:
        n.status = "Read"
        db.commit()
        db.refresh(n)
    return n
