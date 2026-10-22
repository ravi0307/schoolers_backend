"""SMTP email helpers shared across microservices.

All formatting comes from `common.email_templates`, so every message gets a
specific subject, a structured body (greeting, context, details/next steps,
closing) and the right signature — rendered as a plain-text/HTML pair.

Delivery modes:
- `dispatch_email(db, ...)` — outbox-backed: one row per logical message in
  `email_deliveries` with an explicit Pending/Sent/Failed state, payload-hash
  dedupe so retries never create a duplicate delivery, and bounded
  opportunistic retry. Use for mail whose body is safe to persist.
- Direct send (`db=None` helpers / `try_send_email`) — best-effort, never
  raises. Use for mail whose body carries live secrets (password-reset OTPs,
  temporary credentials) that must not be stored at rest.
"""
import hashlib
import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from common.config import settings
from common.email_templates import (
    collapse,
    platform_signature,
    platform_subject,
    render_email,
    school_signature,
    school_subject,
    truncate_words,
)
from common.models import EmailDelivery

logger = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 5
RETRY_MIN_AGE = timedelta(seconds=120)
IDEMPOTENCY_WINDOW = timedelta(minutes=10)

# Fallback subject purposes when an announcement body has no usable first line.
_NOTIFICATION_PURPOSES = {
    "Dues": "Dues Reminder",
    "Activation": "Account Activation Notice",
    "General": "General Announcement",
}


def is_email_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_FROM)


def school_recipients(school) -> list[str]:
    recipients: list[str] = []
    if school.primary_email:
        recipients.append(school.primary_email.strip())
    alt = getattr(school, "alternative_email", None)
    if alt:
        recipients.append(alt.strip())
    return list(dict.fromkeys(addr for addr in recipients if addr))


def _from_header() -> str:
    """Consistent sender identity: '{SMTP_FROM_NAME} <SMTP_FROM>' unless the
    configured address already carries a display name."""
    address = settings.SMTP_FROM
    if "<" in address:
        return address
    name = collapse(getattr(settings, "SMTP_FROM_NAME", "") or "")
    return f"{name} <{address}>" if name else address


def send_email(to_addrs: list[str], subject: str, body: str, html: str | None = None) -> None:
    if not to_addrs:
        raise ValueError("No recipient email addresses")
    if not is_email_configured():
        raise RuntimeError(
            "Email is not configured. Set SMTP_HOST and SMTP_FROM in common/.env"
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = _from_header()
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")

    if settings.SMTP_USE_TLS:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            if settings.SMTP_USER:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as smtp:
            if settings.SMTP_USER:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            smtp.send_message(msg)

    # Subject and recipients only: bodies can contain OTPs or credentials.
    logger.info("Email sent to %s — %s", to_addrs, subject)


def send_school_notification_email(
    school_name: str,
    notif_type: str,
    message: str,
    recipients: list[str],
    *,
    db=None,
    school_email: str | None = None,
    school_phone: str | None = None,
) -> bool:
    """School-context announcement email; subject derives from the message."""
    first_line = next((ln for ln in str(message).splitlines() if ln.strip()), "")
    if collapse(first_line):
        purpose = truncate_words(collapse(first_line))
    else:
        purpose = _NOTIFICATION_PURPOSES.get(notif_type, "School Announcement")
    subject = school_subject(school_name, purpose)
    paragraphs = [para.strip() for para in str(message).split("\n\n") if para.strip()]
    text, html = render_email(
        greeting="Hello,",
        paragraphs=[
            f"This is a {collapse(notif_type).lower()} message about "
            f"{collapse(school_name)}, sent through Schoolers.",
            *paragraphs,
        ],
        closing="You do not need to take any action unless the message above asks you to.",
        signature=school_signature(school_name, school_email, school_phone),
    )
    return _deliver(db, recipients, subject, text, html)


# ---------------------------------------------------------------------------
# Account-lifecycle notifications (best-effort)
# ---------------------------------------------------------------------------


def _dedupe_recipients(recipients: list[str]) -> list[str]:
    return list(dict.fromkeys(addr.strip() for addr in recipients if addr and addr.strip()))


def _now() -> datetime:
    """Naive UTC, matching the TIMESTAMP columns of the outbox."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _safe_error(exc: Exception) -> str:
    """Outbox-visible error: type only — SMTP text can leak relay details."""
    return type(exc).__name__


def _payload_key(to_addrs: list[str], subject: str, body: str, now: datetime | None = None) -> str:
    """Idempotency key: payload hash bucketed to a 10-minute window.

    Re-dispatching the same logical message within the window (client retry,
    ambiguous failure) hits the same key and reuses the row, so a retry never
    sends twice. Outside the window a genuinely repeated message — the same
    announcement next term, a re-hired staff member — gets its own delivery.
    """
    payload = "\n".join((",".join(sorted(to_addrs)), subject, body))
    moment = now or _now()
    bucket = int(moment.timestamp() // IDEMPOTENCY_WINDOW.total_seconds())
    return hashlib.sha256(f"{payload}\x00{bucket}".encode("utf-8")).hexdigest()[:64]


def _attempt_delivery(db, row) -> bool:
    """Try one send for an outbox row; always records an explicit state."""
    row.attempts += 1
    row.updated_at = _now()
    to_addrs = [addr.strip() for addr in row.recipients.split(",") if addr.strip()]
    try:
        send_email(to_addrs, row.subject, row.body_text, html=row.body_html)
    except Exception as exc:  # noqa: BLE001 - one delivery must not break the caller
        row.state = "Failed"
        row.last_error = _safe_error(exc)
        logger.exception(
            "Email delivery %s failed (attempt %s/%s): %s",
            row.delivery_id, row.attempts, MAX_DELIVERY_ATTEMPTS, row.subject,
        )
        db.commit()
        return False
    row.state = "Sent"
    row.sent_at = _now()
    row.last_error = None
    db.commit()
    return True


def _retry_stale_deliveries(db, limit: int = 2) -> None:
    """Opportunistic bounded retry of earlier Pending/Failed deliveries.

    Runs alongside a fresh dispatch so retries need no separate daemon: any
    later email in the same database sweeps the backlog. Rows already at the
    attempt cap are left as a visible Failed record.
    """
    cutoff = _now() - RETRY_MIN_AGE
    stale = (
        db.query(EmailDelivery)
        .filter(EmailDelivery.state.in_(["Pending", "Failed"]))
        .filter(EmailDelivery.attempts < MAX_DELIVERY_ATTEMPTS)
        .filter(EmailDelivery.updated_at < cutoff)
        .order_by(EmailDelivery.updated_at)
        .limit(limit)
        .all()
    )
    for row in stale:
        _attempt_delivery(db, row)


def dispatch_email(db, to_addrs: list[str], subject: str, body: str, html: str | None = None) -> bool:
    """Outbox-backed delivery with dedupe, explicit states and bounded retry."""
    if not to_addrs:
        logger.warning("Skipping email %r: no recipients", subject)
        return False
    # Deliberately no configured-SMTP gate here: send_email performs that
    # check and raises, so an unconfigured server becomes a visible Failed
    # delivery row instead of a silent skip. (The notification flow
    # pre-checks configuration before creating its row, so it never reaches
    # this path unconfigured.)

    key = _payload_key(to_addrs, subject, body)
    row = db.query(EmailDelivery).filter(EmailDelivery.idempotency_key == key).first()
    if row is not None and row.state == "Sent":
        logger.info("Email delivery %s already sent — duplicate skipped", row.delivery_id)
        return True
    if row is None:
        row = EmailDelivery(
            idempotency_key=key,
            recipients=", ".join(to_addrs),
            subject=subject,
            body_text=body,
            body_html=html,
            state="Pending",
            attempts=0,
            updated_at=_now(),
        )
        db.add(row)
        # Persist before attempting so a crash mid-send leaves a Pending row
        # instead of an unrecorded message.
        db.commit()
    ok = _attempt_delivery(db, row)
    _retry_stale_deliveries(db)
    return ok


def try_send_email(recipients: list[str], subject: str, body: str, html: str | None = None) -> bool:
    """Dispatch an email without ever raising (direct, non-persisted path).

    Account-lifecycle notifications are side effects of a more important
    operation (creating a school, adding a student...) and must not break it
    when SMTP is unconfigured or down, so failures are logged and swallowed.
    Bodies carrying live secrets use this path so nothing is written to the
    outbox at rest.
    """
    to_addrs = _dedupe_recipients(recipients)
    if not to_addrs:
        logger.warning("Skipping email %r: no recipients", subject)
        return False
    try:
        send_email(to_addrs, subject, body, html=html)
        return True
    except Exception as exc:  # noqa: BLE001 - lifecycle mail must never raise
        logger.warning("Could not send %r to %s: %s", subject, to_addrs, _safe_error(exc))
        return False


def _deliver(
    db,
    recipients: list[str],
    subject: str,
    text: str,
    html: str | None = None,
) -> bool:
    """Route one composed message to the right delivery mode."""
    to_addrs = _dedupe_recipients(recipients)
    if not to_addrs:
        logger.warning("Skipping email %r: no recipients", subject)
        return False
    if db is None:
        return try_send_email(to_addrs, subject, text, html=html)
    return dispatch_email(db, to_addrs, subject, text, html)


def send_school_registered_email(
    school_name: str,
    recipients: list[str],
    username: str | None = None,
    password: str | None = None,
) -> bool:
    paragraphs = [
        f"Your school, {collapse(school_name)}, and its administrator account "
        "have been created on the Schoolers platform.",
        "Sign in to the Schoolers portal with your administrator credentials "
        "to set up students, staff, classes, and more.",
    ]
    details: list[str] = []
    steps: list[str] = []
    if username and password:
        paragraphs.append("Sign-in details:")
        details = [
            f"Username: {collapse(username)}",
            f"Temporary password: {collapse(password)}",
        ]
        steps = [
            "Go to the Schoolers sign-in page.",
            'Click "Forgot password?" and enter your username or the email on '
            "file — we will email a 6-digit OTP.",
            "Enter the OTP and set a new password.",
        ]
    text, html = render_email(
        greeting="Hello,",
        paragraphs=paragraphs,
        details=details,
        steps=steps,
        closing="If you did not create this school, please contact support.",
        signature=platform_signature(),
    )
    # Temporary credentials live in the body: direct send, never persisted.
    return _deliver(None, recipients, platform_subject(f"School Account Created: {school_name}"), text, html)


def send_school_removed_email(school_name: str, recipients: list[str], *, db=None) -> bool:
    text, html = render_email(
        greeting="Hello,",
        paragraphs=[
            f"Your school, {collapse(school_name)}, and its administrator "
            "account have been removed from the Schoolers platform.",
        ],
        closing="If this was unexpected, please contact support.",
        signature=platform_signature(),
    )
    return _deliver(db, recipients, platform_subject(f"School Account Removed: {school_name}"), text, html)


def send_staff_added_email(
    school_name: str,
    staff_name: str,
    recipients: list[str],
    *,
    db=None,
    school_email: str | None = None,
    school_phone: str | None = None,
) -> bool:
    text, html = render_email(
        greeting=f"Hello {collapse(staff_name)},",
        paragraphs=[
            f"You have been added as a staff member at {collapse(school_name)} "
            "on the Schoolers platform.",
        ],
        closing="If this was not expected, please contact your school administrator.",
        signature=school_signature(school_name, school_email, school_phone),
    )
    return _deliver(
        db, recipients,
        school_subject(school_name, f"Staff Account Created: {staff_name}"),
        text, html,
    )


def send_staff_removed_email(
    school_name: str,
    staff_name: str,
    recipients: list[str],
    *,
    db=None,
    school_email: str | None = None,
    school_phone: str | None = None,
) -> bool:
    text, html = render_email(
        greeting=f"Hello {collapse(staff_name)},",
        paragraphs=[
            f"Your staff access for {collapse(school_name)} on the Schoolers "
            "platform has been removed.",
        ],
        closing="If this was unexpected, please contact your school administrator.",
        signature=school_signature(school_name, school_email, school_phone),
    )
    return _deliver(
        db, recipients,
        school_subject(school_name, f"Staff Access Removed: {staff_name}"),
        text, html,
    )


def send_student_added_email(
    school_name: str,
    student_name: str,
    recipients: list[str],
    *,
    db=None,
    school_email: str | None = None,
    school_phone: str | None = None,
    recipient_name: str | None = None,
) -> bool:
    greeting = f"Hello {collapse(recipient_name)}," if recipient_name else "Hello,"
    text, html = render_email(
        greeting=greeting,
        paragraphs=[
            f"{collapse(student_name)} has been enrolled at "
            f"{collapse(school_name)} on the Schoolers platform.",
            "You can follow their attendance, marks, and timetable from your "
            "Schoolers account.",
        ],
        closing="There is nothing you need to do for this enrolment.",
        signature=school_signature(school_name, school_email, school_phone),
    )
    return _deliver(
        db, recipients,
        school_subject(school_name, f"Student Enrolled: {student_name}"),
        text, html,
    )


def send_student_removed_email(
    school_name: str,
    student_name: str,
    recipients: list[str],
    *,
    db=None,
    school_email: str | None = None,
    school_phone: str | None = None,
    recipient_name: str | None = None,
) -> bool:
    greeting = f"Hello {collapse(recipient_name)}," if recipient_name else "Hello,"
    text, html = render_email(
        greeting=greeting,
        paragraphs=[
            f"{collapse(student_name)} has been withdrawn from "
            f"{collapse(school_name)} on the Schoolers platform.",
        ],
        closing="If this was unexpected, please contact the school.",
        signature=school_signature(school_name, school_email, school_phone),
    )
    return _deliver(
        db, recipients,
        school_subject(school_name, f"Student Withdrawn: {student_name}"),
        text, html,
    )


FIELD_LABELS: dict[str, str] = {
    "name": "Name",
    "admission_no": "Admission number",
    "class_id": "Class",
    "date_of_birth": "Date of birth",
    "gender": "Gender",
    "photo_url": "Photo",
    "aadhaar_number": "Aadhaar number",
    "birth_certificate_number": "Birth certificate number",
    "documents": "Documents",
    "parent_name": "Parent name",
    "parent_phone": "Parent phone",
    "parent_email": "Parent email",
    "parent_address": "Parent address",
    "parent_emergency_number": "Parent emergency number",
    "email": "Email",
    "email_id": "Email",
    "phone": "Phone",
    "mobile_number": "Mobile number",
    "role": "Role",
    "department": "Department",
    "date_of_joining": "Date of joining",
    "address": "Address",
    "present_address": "Present address",
    "permanent_address": "Permanent address",
    "emergency_number": "Emergency number",
    "marital_status": "Marital status",
    "qualification": "Qualification",
    "primary_email": "Primary email",
    "alternative_email": "Alternative email",
    "dob": "Date of birth",
}


def send_record_updated_email(
    record_type: str,
    record_name: str,
    school_name: str,
    changes: list[tuple[str, str, str]],
    recipients: list[str],
    *,
    db=None,
    school_email: str | None = None,
    school_phone: str | None = None,
) -> bool:
    """Notify the affected person(s) that a record was edited, listing the
    fields that changed with their old and new values (best-effort)."""
    where = f" at {collapse(school_name)}" if school_name != record_name else ""
    text, html = render_email(
        greeting="Hello,",
        paragraphs=[
            f'Your {collapse(record_type).lower()} record "{collapse(record_name)}"'
            f"{where} has been updated.",
        ],
        details=[f"{label}: {old} → {new}" for label, old, new in changes],
        closing="If this change was not made by you, please contact your school administrator.",
        signature=school_signature(school_name, school_email, school_phone),
    )
    return _deliver(
        db, recipients,
        school_subject(school_name, f"{record_type} Record Updated: {record_name}"),
        text, html,
    )
