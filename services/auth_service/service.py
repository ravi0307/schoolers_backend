import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError
from sqlalchemy.orm import Session
from sqlalchemy import func

import common.email
from common.authz import PRIVILEGED_ROLES, SUBMASTER_ROLE, assert_can_manage_privileged
from common.email_templates import platform_signature, platform_subject, render_email
from common.exceptions import ConflictError, NotFoundError, UnauthorizedError
from common.security import (
    verify_password, create_access_token, create_refresh_token, decode_token,
    generate_temp_password, hash_password,
)
from common.models import User, Staff, Parent, School

logger = logging.getLogger(__name__)

RESET_TOKEN_TTL_MINUTES = 30
_GENERIC_INVALID_RESET_MESSAGE = "Invalid or expired reset token."


def authenticate(db: Session, username: str, password: str) -> User:
    # Use password recovery's normalized username/email lookup. Staff can sign in
    # with the email address maintained on their linked staff row.
    user = find_user_by_identifier(db, username)
    if not user or not verify_password(password, user.password_hash):
        raise UnauthorizedError("Invalid username or password")
    if user.role in ("teacher", "staff", "pilot"):
        person = (
            db.query(Staff)
            .filter(
                Staff.staff_id == user.linked_person_id,
                Staff.school_id == user.school_id,
                Staff.is_active.is_(True),
            )
            .first()
        )
        if not person:
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

    # linked_person_id is only unique within its own table, so match the
    # account role and its person table together to stop an email from
    # selecting an unrelated account that happens to share the numeric id.
    # Teachers, staff, admins and pilots all resolve through the staff table.
    lookups = (
        ("teacher", Staff, Staff.staff_id),
        ("staff", Staff, Staff.staff_id),
        ("admin", Staff, Staff.staff_id),
        ("pilot", Staff, Staff.staff_id),
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
    return None


def display_name_for(db: Session, role: str, linked_person_id: int | None) -> str | None:
    """The human name of whoever holds an account, or None.

    The users table stores no name of its own, so it is resolved from the
    linked person row. Which table that is depends on the role, and
    linked_person_id is only unique within its own table, so the role decides
    the lookup. Students have no accounts and masters have no linked person, so
    both fall through to None and the caller falls back to the username.
    """
    if linked_person_id is None:
        return None
    if role in ("teacher", "staff", "admin", "pilot"):
        person = db.query(Staff).filter(Staff.staff_id == linked_person_id).first()
        return person.name if person else None
    if role == "parent":
        person = db.query(Parent).filter(Parent.parent_id == linked_person_id).first()
        return person.name if person else None
    return None


def account_email_address(db: Session, user: User) -> str | None:
    """The address on file for an account, preferring users.email.

    A user row may carry its own email, but staff and parent rows also hold
    one, and some accounts are created without a users.email. Prefer the one
    on the account itself so the profile shows what the account actually uses,
    then fall back to the linked person. Admin accounts resolve to their
    school's primary email, matching user_email_address().
    """
    if user.email:
        return user.email
    if user.role == "admin":
        school = db.query(School).filter(School.school_id == user.school_id).first()
        return school.primary_email if school else None
    if user.role in ("teacher", "staff", "pilot"):
        person = db.query(Staff).filter(Staff.staff_id == user.linked_person_id).first()
        return person.email if person else None
    if user.role == "parent":
        person = db.query(Parent).filter(Parent.parent_id == user.linked_person_id).first()
        return person.email if person else None
    return None


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    """Replace a signed-in user's own password.

    The current password must verify first, so possession of a valid access
    token alone is not enough to rotate the credential. Any outstanding reset
    OTP is cleared at the same time, otherwise a code issued before the change
    could still be redeemed afterwards.
    """
    if not verify_password(current_password, user.password_hash):
        raise UnauthorizedError("Current password is incorrect")
    user.password_hash = hash_password(new_password)
    user.password_reset_token = None
    user.password_reset_token_expires_at = None
    db.add(user)
    db.commit()


def user_email_address(db: Session, user: User) -> str | None:
    """Resolve a deliverable email address for an account, or None.

    Students have no account/email of their own; master accounts are global and
    have none either. Sub-Master accounts are standalone (no linked person) but
    may carry an address on their own row, so password recovery can reach them.
    """
    if user.role == "submaster":
        return user.email or None
    if user.role == "admin":
        school = db.query(School).filter(School.school_id == user.school_id).first()
        return school.primary_email if school else None
    if user.role in ("teacher", "staff", "pilot"):
        person = db.query(Staff).filter(Staff.staff_id == user.linked_person_id).first()
        return person.email if person else None
    if user.role == "parent":
        person = db.query(Parent).filter(Parent.parent_id == user.linked_person_id).first()
        return person.email if person else None
    return None


def _deliver_reset_token(email: str, raw_token: str, expires_minutes: int = RESET_TOKEN_TTL_MINUTES) -> None:
    """Email the one-time 6-digit reset OTP out of band. Best-effort: a
    delivery failure is logged and swallowed so it can never reveal whether
    the identifier is registered. Sent directly (not via the outbox) so the
    raw OTP is never persisted to the database at rest."""
    subject = platform_subject("Password Reset Request")
    text, html = render_email(
        greeting="Hello,",
        paragraphs=[
            "A password reset was requested for your Schoolers account.",
        ],
        details=[
            f"Your one-time 6-digit code is: {raw_token}",
            f"The code expires in {expires_minutes} minutes and can only be used once.",
        ],
        steps=[
            'Open the Schoolers sign-in page and click "Forgot password?".',
            "Enter your username or the email on file.",
            "Enter this code and set a new password.",
        ],
        closing="If you did not request this, you can safely ignore this email.",
        signature=platform_signature(),
    )
    common.email.try_send_email([email], subject, text, html=html)


def _reset_token_digest(raw_token: str) -> str:
    """One-way digest so a leaked users row cannot be used to reset logins."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _utcnow_naive() -> datetime:
    """`users.password_reset_token_expires_at` is a timezone-naive column, so
    compare against naive UTC consistently (Postgres and SQLite both return
    naive datetimes for it)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _issue_reset_token(db: Session, user: User) -> tuple[str, datetime]:
    """Issue a fresh one-time 6-digit reset OTP and return (raw_otp, expires_at)."""
    raw_token = f"{secrets.randbelow(1_000_000):06d}"
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
    6-digit reset OTP is issued and emailed to the account's address on file.
    The raw OTP is never returned in an API response — it is delivered out of
    band so merely knowing the identifier is not enough to take over the
    account.
    """
    user = find_user_by_identifier(db, identifier)
    if user is None:
        return None, None
    raw_token, expires_at = _issue_reset_token(db, user)
    email = user_email_address(db, user)
    if email:
        _deliver_reset_token(email, raw_token)
    return raw_token, expires_at


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


# ---- Sub-Master accounts: the Master-managed lifecycle ----


def _account_dict(user: User) -> dict:
    return {
        "user_id": user.user_id,
        "username": user.username,
        "role": user.role,
        "email": user.email,
        "is_active": user.is_active,
        "created_at": user.created_at,
    }


def list_privileged_accounts(db: Session) -> list[dict]:
    """Every Master and Sub-Master account, for the Master-facing roster."""
    rows = (
        db.query(User)
        .filter(User.role.in_(PRIVILEGED_ROLES))
        .order_by(User.role, User.username)
        .all()
    )
    return [_account_dict(user) for user in rows]


def create_submaster(db: Session, payload) -> dict:
    """Create a Sub-Master (router already restricted to Master callers).

    The role is assigned here and never read from the payload, so the
    endpoint cannot mint a Master or any other role no matter what a client
    sends. The password — supplied or generated — is returned exactly once
    with the creation response; only its bcrypt hash is persisted.
    """
    if (
        db.query(User.user_id)
        .filter(func.lower(User.username) == payload.username.lower())
        .first()
    ):
        raise ConflictError("Username is already taken")
    password = payload.password or generate_temp_password()
    user = User(
        school_id=None,
        role=SUBMASTER_ROLE,
        username=payload.username,
        email=payload.email or None,
        password_hash=hash_password(password),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {**_account_dict(user), "password": password}


def _target_submaster(db: Session, current_user, user_id: int, action: str) -> User:
    """Fetch the target account and enforce the management rule.

    Unknown ids are 404. A Master target, an account of any other role, or a
    non-Master actor are all the same generic 403 from
    assert_can_manage_privileged — the response never describes what the id
    points at. Master accounts have no mutation API at all: even a Master
    actor may only mutate Sub-Master rows here.
    """
    user = db.query(User).filter(User.user_id == user_id).first()
    if user is None:
        raise NotFoundError("Account not found")
    assert_can_manage_privileged(current_user, user.role, action)
    return user


def update_submaster(db: Session, current_user, user_id: int, payload) -> dict:
    """Apply a partial update to a Sub-Master: email, activation, password."""
    user = _target_submaster(db, current_user, user_id, "update")
    if payload.email is not None:
        user.email = payload.email.strip() or None
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
        user.password_reset_token = None
        user.password_reset_token_expires_at = None
    db.add(user)
    db.commit()
    db.refresh(user)
    return _account_dict(user)


def deactivate_submaster(db: Session, current_user, user_id: int) -> None:
    """Deactivate a Sub-Master — the system's soft-delete for users.

    Rows are never destroyed (a deactivated account keeps its audit trail and
    can be reactivated with PATCH is_active=true); deactivation immediately
    blocks new logins because identifier lookup only matches active rows.
    """
    user = _target_submaster(db, current_user, user_id, "delete")
    user.is_active = False
    db.add(user)
    db.commit()
