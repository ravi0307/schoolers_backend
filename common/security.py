"""
Password hashing and JWT issuing/verification.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import jwt, JWTError
import bcrypt

from common.config import settings

def hash_password(plain: str) -> str:
    """Create a bcrypt password hash using the installed bcrypt library."""
    password = plain.encode("utf-8")
    if len(password) > 72:
        raise ValueError("Password must be 72 bytes or fewer.")
    return bcrypt.hashpw(password, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _create_token(data: dict[str, Any], expires_delta: timedelta, token_type: str) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode.update({"exp": expire, "type": token_type})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: int, role: str, school_id: int | None, linked_person_id: int | None = None) -> str:
    return _create_token(
        {"sub": str(user_id), "role": role, "school_id": school_id, "linked_person_id": linked_person_id},
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "access",
    )


def create_refresh_token(user_id: int) -> str:
    return _create_token(
        {"sub": str(user_id)},
        timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES),
        "refresh",
    )


def decode_token(token: str) -> dict[str, Any]:
    """Raises jose.JWTError if invalid/expired — caller maps to HTTP 401."""
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
