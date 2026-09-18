import secrets
import time
from datetime import datetime, timezone

from jose import JWTError
from sqlalchemy.orm import Session
from sqlalchemy import func

from common.exceptions import UnauthorizedError, AppError
from common.security import (
    verify_password, create_access_token, create_refresh_token, decode_token,
    hash_password,
)
from common.models import User, Teacher, Staff, Parent, Pilot, School
from common.email import send_email

# One-time password store for the forgot-password flow. In-memory is fine for a
# single auth-service process; a multi-instance deployment should back this
# with Redis or a database table instead.
OTP_EXPIRE_SECONDS = 15 * 60
_otp_store: dict[int, dict] = {}  # user_id -> {"otp": str, "expires_at": float}


def authenticate(db: Session, username: str, password: str) -> User:
    user = db.query(User).filter(User.username == username, User.is_active.is_(True)).first()
    if not user or not verify_password(password, user.password_hash):
        raise UnauthorizedError("Invalid username or password")
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    return user


def issue_tokens(user: User) -> dict:
    return {
        "access_token": create_access_token(user.user_id, user.role, user.school_id, user.linked_person_id),
        "refresh_token": create_refresh_token(user.user_id),
        "role": user.role,
        "school_id": user.school_id,
        "user_id": user.user_id,
        "linked_person_id": user.linked_person_id,
    }


def refresh_access_token(db: Session, refresh_token: str) -> str:
    try:
        payload = decode_token(refresh_token)
    except JWTError:
        raise UnauthorizedError("Invalid or expired refresh token")
    if payload.get("type") != "refresh":
        raise UnauthorizedError("Not a refresh token")
    user = db.query(User).filter(User.user_id == int(payload["sub"]), User.is_active.is_(True)).first()
    if not user:
        raise UnauthorizedError("User no longer exists")
    return create_access_token(user.user_id, user.role, user.school_id, user.linked_person_id)


def find_user_by_identifier(db: Session, identifier: str) -> User | None:
    """Return the active account matching a username or linked email address."""
    normalized_identifier = identifier.strip().lower()

    # Usernames exist directly on the users table, including admin and master
    # accounts which may not have a linked person record or email address.
    user = (
        db.query(User)
        .filter(
            User.is_active.is_(True),
            func.lower(User.username) == normalized_identifier,
        )
        .first()
    )
    if user:
        return user

    # IDs are only unique within their own table.  Match both the account role
    # and its corresponding person table to prevent an email from selecting an
    # unrelated account with the same numeric linked_person_id.
    lookups = (
        ("teacher", Teacher, Teacher.teacher_id),
        ("staff", Staff, Staff.staff_id),
        ("parent", Parent, Parent.parent_id),
    )
    for role, person_model, person_id in lookups:
        user = (
            db.query(User)
            .join(person_model, User.linked_person_id == person_id)
            .filter(
                User.role == role,
                User.is_active.is_(True),
                func.lower(person_model.email) == normalized_identifier,
            )
            .first()
        )
        if user:
            return user

    # Pilots link directly to users through Pilot.user_id rather than
    # User.linked_person_id.
    return (
        db.query(User)
        .join(Pilot, Pilot.user_id == User.user_id)
        .filter(
            User.role == "pilot",
            User.is_active.is_(True),
            func.lower(Pilot.email) == normalized_identifier,
        )
        .first()
    )


# ---------------------------------------------------------------------------
# Forgot-password flow (email a 6-digit OTP, verify it, then reset)
# ---------------------------------------------------------------------------

def user_email_address(db: Session, user: User) -> str | None:
    """Resolve a deliverable email address for an account, or None."""
    if user.role == "admin":
        school = db.query(School).filter(School.school_id == user.school_id).first()
        return school.primary_email if school else None
    if user.role == "teacher":
        person = db.query(Teacher).filter(Teacher.teacher_id == user.linked_person_id).first()
        return person.email if person else None
    if user.role == "staff":
        person = db.query(Staff).filter(Staff.staff_id == user.linked_person_id).first()
        return person.email if person else None
    if user.role == "parent":
        person = db.query(Parent).filter(Parent.parent_id == user.linked_person_id).first()
        return person.email if person else None
    if user.role == "pilot":
        pilot = db.query(Pilot).filter(Pilot.user_id == user.user_id).first()
        return pilot.email if pilot else None
    return None


def _store_otp(user_id: int) -> str:
    otp = f"{secrets.randbelow(1_000_000):06d}"
    _otp_store[user_id] = {"otp": otp, "expires_at": time.monotonic() + OTP_EXPIRE_SECONDS}
    return otp


def _consume_otp(user_id: int, otp: str) -> bool:
    entry = _otp_store.get(user_id)
    if not entry:
        return False
    if time.monotonic() > entry["expires_at"] or entry["otp"] != otp:
        _otp_store.pop(user_id, None)
        return False
    _otp_store.pop(user_id, None)
    return True


def forgot_password_request(db: Session, identifier: str) -> tuple[User | None, str]:
    """
    Step 1: Look up *identifier* (username or email), generate a 6-digit OTP,
    and email it to the account's registered address.
    """
    user = find_user_by_identifier(db, identifier)
    if user is None:
        # Don't reveal whether the account exists — return a generic message.
        return None, "If the username or email is registered, an OTP has been sent."

    email = user_email_address(db, user)
    if not email:
        raise AppError(
            "No email address is on file for this account. "
            "Contact your school administrator."
        )

    otp = _store_otp(user.user_id)
    try:
        send_email(
            [email],
            "Schoolers — Password Reset OTP",
            (
                f"Hello {user.username},\n\n"
                f"Your Schoolers account password reset one-time password (OTP) is:\n\n"
                f"   {otp}\n\n"
                f"Enter it on the reset screen to choose a new password. "
                f"This code expires in 15 minutes.\n\n"
                f"If you did not request a password reset, you can ignore this email.\n\n"
                f"— Schoolers Platform"
            ),
        )
    except Exception as exc:  # noqa: BLE001 - surface the send failure to the user
        _otp_store.pop(user.user_id, None)
        raise AppError(f"Could not send the reset OTP email: {exc}") from exc

    return user, f"An OTP has been sent to the email on file for '{user.username}'."


def forgot_password_reset(db: Session, identifier: str, otp: str, new_password: str) -> User:
    """
    Step 2: Verify the OTP and set a new password for the matching account.
    """
    user = find_user_by_identifier(db, identifier)
    if user is None:
        raise UnauthorizedError("No user found with that username or email address.")
    if not _consume_otp(user.user_id, otp):
        raise UnauthorizedError("Invalid or expired OTP. Please request a new one.")
    user.password_hash = hash_password(new_password)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user