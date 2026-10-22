"""Login provisioning for onboarding flows.

The API historically created no logins for staff, teachers or parents (only
the seed script inserted them), so onboarding emails had no credentials to
carry. These helpers give every newly created person a username and a
human-typeable temporary password at the moment their record is committed,
so the onboarding email can deliver working sign-in details. The password is
returned once to the caller for delivery and never stored anywhere but as a
bcrypt hash; if it is lost, the forgot-password flow (username or email plus
a one-time OTP) still recovers the account.
"""
import re

from sqlalchemy.orm import Session

from common.models import User
from common.security import generate_temp_password, hash_password


def username_base(name: str, fallback: str) -> str:
    """first.lastname from a display name, lowercased, or *fallback*.

    Multi-word names keep the first two words; single words are used as-is.
    Anything outside [a-z0-9._] is dropped so a name can never smuggle spaces
    or punctuation into the unique username column.
    """
    words = str(name or "").split()
    if words:
        base = ".".join(words[:2]) if len(words) > 1 else words[0]
        base = re.sub(r"[^a-z0-9._]", "", base.lower())
        if base:
            return base
    return fallback


def unique_username(db: Session, base: str) -> str:
    """*base*, suffixed (2, 3, …) until free in the users table."""
    candidate = base
    n = 2
    while db.query(User.user_id).filter(User.username == candidate).first():
        candidate = f"{base}{n}"
        n += 1
    return candidate


def provision_login(
    db: Session,
    *,
    school_id: int,
    role: str,
    name: str,
    email: str | None,
    linked_person_id: int,
) -> tuple[str, str] | None:
    """Create an active login for a person; returns (username, temp_password).

    Returns None when an active login already exists for that role and
    linked person — a duplicate onboarding must never mint a second account
    or replace a password someone is already using.
    """
    existing = (
        db.query(User.user_id)
        .filter(
            User.role == role,
            User.linked_person_id == linked_person_id,
            User.is_active.is_(True),
        )
        .first()
    )
    if existing:
        return None
    base = username_base(name, fallback=f"{role}.{linked_person_id}")
    username = unique_username(db, base)
    password = generate_temp_password()
    db.add(
        User(
            school_id=school_id,
            role=role,
            username=username,
            email=email or None,
            password_hash=hash_password(password),
            linked_person_id=linked_person_id,
            is_active=True,
        )
    )
    db.flush()
    return username, password
