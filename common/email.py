"""SMTP email helpers shared across microservices."""
import logging
import smtplib
from email.message import EmailMessage

from common.config import settings

logger = logging.getLogger(__name__)


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


def send_email(to_addrs: list[str], subject: str, body: str) -> None:
    if not to_addrs:
        raise ValueError("No recipient email addresses")
    if not is_email_configured():
        raise RuntimeError(
            "Email is not configured. Set SMTP_HOST and SMTP_FROM in common/.env"
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(body)

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

    logger.info("Email sent to %s — %s", to_addrs, subject)


def send_school_notification_email(
    school_name: str,
    notif_type: str,
    message: str,
    recipients: list[str],
) -> None:
    subject = f"Schoolers — {notif_type} notification for {school_name}"
    body = (
        f"Hello,\n\n"
        f"You have a new {notif_type} notification from Schoolers for {school_name}.\n\n"
        f"{message}\n\n"
        f"— Schoolers Platform"
    )
    send_email(recipients, subject, body)


# ---------------------------------------------------------------------------
# Account-lifecycle notifications (best-effort)
# ---------------------------------------------------------------------------

def _dedupe_recipients(recipients: list[str]) -> list[str]:
    return list(dict.fromkeys(addr.strip() for addr in recipients if addr and addr.strip()))


def try_send_email(recipients: list[str], subject: str, body: str) -> bool:
    """Dispatch an email without ever raising.

    Account-lifecycle notifications are side effects of a more important
    operation (creating a school, adding a student...) and must not break it
    when SMTP is unconfigured or down, so failures are logged and swallowed.
    """
    to_addrs = _dedupe_recipients(recipients)
    if not to_addrs:
        logger.warning("Skipping email %r: no recipients", subject)
        return False
    try:
        send_email(to_addrs, subject, body)
        return True
    except Exception as exc:  # noqa: BLE001 - lifecycle mail must never raise
        logger.warning("Could not send %r to %s: %s", subject, to_addrs, exc)
        return False


def send_school_registered_email(school_name: str, recipients: list[str]) -> bool:
    return try_send_email(
        recipients,
        subject=f"Schoolers — Welcome, {school_name}!",
        body=(
            f"Hello,\n\n"
            f"Your school, {school_name}, and its administrator account have been "
            f"created on the Schoolers platform.\n\n"
            f"Sign in to the Schoolers portal with your administrator credentials "
            f"to set up students, staff, classes, and more.\n\n"
            f"— Schoolers Platform"
        ),
    )


def send_school_removed_email(school_name: str, recipients: list[str]) -> bool:
    return try_send_email(
        recipients,
        subject=f"Schoolers — {school_name} has been removed",
        body=(
            f"Hello,\n\n"
            f"Your school, {school_name}, and its administrator account have been "
            f"removed from the Schoolers platform. If this was unexpected, please "
            f"contact support.\n\n"
            f"— Schoolers Platform"
        ),
    )


def send_staff_added_email(school_name: str, staff_name: str, recipients: list[str]) -> bool:
    return try_send_email(
        recipients,
        subject=f"Schoolers — Staff account added for {staff_name}",
        body=(
            f"Hello {staff_name},\n\n"
            f"You have been added as staff member at {school_name} on the Schoolers "
            f"platform.\n\n— Schoolers Platform"
        ),
    )


def send_staff_removed_email(school_name: str, staff_name: str, recipients: list[str]) -> bool:
    return try_send_email(
        recipients,
        subject=f"Schoolers — Staff access removed for {staff_name}",
        body=(
            f"Hello {staff_name},\n\n"
            f"Your staff access for {school_name} on the Schoolers platform has been "
            f"removed. If this was unexpected, please contact your school administrator.\n\n"
            f"— Schoolers Platform"
        ),
    )


def send_student_added_email(school_name: str, student_name: str, recipients: list[str]) -> bool:
    return try_send_email(
        recipients,
        subject=f"Schoolers — {student_name} has been enrolled",
        body=(
            f"Hello,\n\n"
            f"{student_name} has been enrolled at {school_name} on the Schoolers "
            f"platform. You can now follow their attendance, marks, and timetable.\n\n"
            f"— Schoolers Platform"
        ),
    )


def send_student_removed_email(school_name: str, student_name: str, recipients: list[str]) -> bool:
    return try_send_email(
        recipients,
        subject=f"Schoolers — {student_name} has been withdrawn",
        body=(
            f"Hello,\n\n"
            f"{student_name} has been withdrawn from {school_name} on the Schoolers "
            f"platform. If this was unexpected, please contact the school.\n\n"
            f"— Schoolers Platform"
        ),
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
) -> bool:
    """Notify the affected person(s) that a record was edited, listing the
    fields that changed with their old and new values (best-effort)."""
    where = f" at {school_name}" if school_name != record_name else ""
    lines = [
        "Hello,",
        "",
        f'Your {record_type.lower()} record "{record_name}"{where} has been updated.',
        "",
        "Changed details:",
    ]
    for label, old, new in changes:
        lines.append(f"  • {label}: {old} → {new}")
    lines.extend(
        [
            "",
            "If this wasn't you, please contact your school administrator.",
            "",
            "— Schoolers Platform",
        ]
    )
    subject = f"Schoolers — {record_type} record updated: {record_name}"
    return try_send_email(recipients, subject, "\n".join(lines))
