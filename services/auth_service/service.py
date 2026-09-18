import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError
from sqlalchemy.orm import Session
from sqlalchemy import func

from common.exceptions import UnauthorizedError
from common.security import (
    verify_password, create_access_token, create_refresh_token, decode_token,
    hash_password,
)
from common.models import User, Teacher, Staff, Parent, Pilot

RESET_TOKEN_TTL_MINUTES = 30
_GENERIC_INVALID_RESET_MESSAGE = "Invalid or expired reset token."


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
        ("admin", Staff, Staff.staff_id),
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


def _reset_token_digest(raw_token: str) -> str:
    """One-way digest so a leaked users row cannot be used to reset logins."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _utcnow_naive() -> datetime:
    """`users.password_reset_token_expires_at` is a timezone-naive column, so
    compare against naive UTC consistently (Postgres and SQLite both return
    naive datetimes for it)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _issue_reset_token(db: Session, user: User) -> tuple[str, datetime]:
    """Issue a fresh one-time reset token and return (raw_token, expires_at)."""
    raw_token = secrets.token_urlsafe(32)
    expires_at = _utcnow_naive() + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)
    user.password_reset_token = _reset_token_digest(raw_token)
    user.password_reset_token_expires_at = expires_at
    db.add(user)
    db.commit()
    return raw_token, expires_at


def forgot_password_request(db: Session, identifier: str) -> tuple[str | None, datetime | None]:
    """
    Step 1: Verify that *identifier* is a username or email in the system.

    An anonymous caller can never tell registered accounts apart: the message
    is identical either way. When the account exists a one-time, short-lived
    reset token is issued and returned so the caller can set a new password.
    In a real deployment the raw token would be emailed instead.
    """
    user = find_user_by_identifier(db, identifier)
    if user is None:
        return None, None
    return _issue_reset_token(db, user)


def _consume_reset_token(db: Session, user: User, raw_token: str) -> None:
    """Validate the supplied raw token against the stored digest and expiry."""
    if (
        not user.password_reset_token
        or not user.password_reset_token_expires_at
        or user.password_reset_token_expires_at < _utcnow_naive()
    ):
        raise UnauthorizedError(_GENERIC_INVALID_RESET_MESSAGE)
    expected = _reset_token_digest(raw_token)
    if not secrets.compare_digest(expected, user.password_reset_token):
        raise UnauthorizedError(_GENERIC_INVALID_RESET_MESSAGE)


def forgot_password_reset(db: Session, identifier: str, reset_token: str, new_password: str) -> User:
    """
    Step 3: Reset the password for the account matching *identifier*, but only
    when the caller also holds the one-time token issued at step 1. The failure
    and blank responses are identical whether the identifier is known or not.
    """
    user = find_user_by_identifier(db, identifier)
    if user is None:
        raise UnauthorizedError(_GENERIC_INVALID_RESET_MESSAGE)
    _consume_reset_token(db, user, reset_token)
    user.password_hash = hash_password(new_password)
    user.password_reset_token = None
    user.password_reset_token_expires_at = None
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
