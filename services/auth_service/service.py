from datetime import datetime, timezone

from jose import JWTError
from sqlalchemy.orm import Session

from common.exceptions import UnauthorizedError
from common.security import (
    verify_password, create_access_token, create_refresh_token, decode_token,
)
from common.models import User


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
