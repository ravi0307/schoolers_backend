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
