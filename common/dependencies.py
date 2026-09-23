"""
FastAPI dependencies for authentication and tenancy scoping.
Every protected route depends on `get_current_user`, and most also apply
`require_role(...)`. School-scoped modules read `current_user.school_id`
from here rather than trusting a school_id in the request.
"""
from dataclasses import dataclass

from fastapi import Depends, Header
from jose import JWTError
from sqlalchemy.orm import Session

from common.audit import set_current_actor
from common.database import get_db
from common.security import decode_token
from common.exceptions import UnauthorizedError, ForbiddenError


@dataclass
class CurrentUser:
    user_id: int
    role: str
    school_id: int | None
    linked_person_id: int | None = None


# async on purpose: a sync dependency runs in a threadpool with a copied
# context, which would discard set_current_actor() below — as async it runs in
# the request's event-loop context so the audit listener sees the actor.
async def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("Missing or malformed Authorization header")
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_token(token)
    except JWTError:
        raise UnauthorizedError("Invalid or expired token")
    if payload.get("type") != "access":
        raise UnauthorizedError("Not an access token")
    user_id = int(payload["sub"])
    set_current_actor(user_id)
    return CurrentUser(
        user_id=user_id,
        role=payload["role"],
        school_id=payload.get("school_id"),
        linked_person_id=payload.get("linked_person_id"),
    )


def require_role(*allowed_roles: str):
    """Dependency factory: require_role("admin", "master") etc."""
    def _checker(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in allowed_roles:
            raise ForbiddenError(f"Role '{current_user.role}' cannot access this endpoint")
        return current_user
    return _checker


def require_school_scope(current_user: CurrentUser = Depends(get_current_user)) -> int:
    """For endpoints scoped to the caller's own school (everyone except master)."""
    if current_user.role == "master":
        raise ForbiddenError("Master Admin must specify a school_id explicitly")
    if current_user.school_id is None:
        raise ForbiddenError("User is not associated with a school")
    return current_user.school_id
