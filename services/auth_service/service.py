from datetime import datetime, timezone

from jose import JWTError
from sqlalchemy.orm import Session
from sqlalchemy import func

from common.exceptions import UnauthorizedError
from common.security import (
    verify_password, create_access_token, create_refresh_token, decode_token,
    hash_password,
)
from common.models import User, Teacher, Staff, Parent, Pilot


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


def forgot_password_request(db: Session, identifier: str) -> tuple[User | None, str]:
    """
    Step 1: Verify that *identifier* is a username or email in the system.
    Returns (user, message).
    """
    user = find_user_by_identifier(db, identifier)
    if user is None:
        # Don't reveal whether the email exists — return a generic message.
        return None, "If the username or email is registered, you can reset the password."
    # In a real deployment you would email a one-time token here.
    return user, f"Account '{user.username}' was found. You can now set a new password."


def forgot_password_reset(db: Session, identifier: str, new_password: str) -> User:
    """
    Step 2: Reset the password for the account matching *identifier*.
    """
    user = find_user_by_identifier(db, identifier)
    if user is None:
        raise UnauthorizedError("No user found with that username or email address.")
    user.password_hash = hash_password(new_password)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
