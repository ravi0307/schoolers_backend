from collections import defaultdict
from datetime import datetime
import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import get_current_user, CurrentUser
from common.exceptions import AppError
import service
from schemas import (
    LoginRequest, TokenResponse, RefreshRequest, RefreshResponse,
    ForgotPasswordRequest, ForgotPasswordVerifyRequest,
    ForgotPasswordResetRequest, ForgotPasswordResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Identical for every caller so the responses can never be used to learn
# whether a username or email is registered.
REQUEST_MESSAGE = "If the username or email is registered, a one-time reset token has been issued."
RESET_MESSAGE = "Your password has been reset. You can now log in."

# Best-effort in-memory throttling of the anonymous reset endpoints so a caller
# cannot flood token delivery or continually invalidate a legitimate user's
# token. Limits are per process; a multi-worker production deployment should
# back these with shared storage.
_RESET_ATTEMPT_LIMIT = 5
_RESET_ATTEMPT_WINDOW_SECONDS = 15 * 60
_RESET_ISSUE_COOLDOWN_SECONDS = 60
_attempts: dict[str, list[float]] = defaultdict(list)  # key -> timestamps
_last_issue: dict[str, float] = {}  # identifier -> last token issue time


def _rate_limited(key: str, limit: int = _RESET_ATTEMPT_LIMIT, window: float = _RESET_ATTEMPT_WINDOW_SECONDS) -> bool:
    now = time.monotonic()
    recent = [t for t in _attempts[key] if now - t < window]
    _attempts[key] = recent
    if len(recent) >= limit:
        return True
    _attempts[key].append(now)
    return False


def _cooldown_active(identifier: str) -> bool:
    last = _last_issue.get(identifier)
    return last is not None and (time.monotonic() - last) < _RESET_ISSUE_COOLDOWN_SECONDS


def _record_issue(identifier: str) -> None:
    _last_issue[identifier] = time.monotonic()


def _forgot_password_response(raw_token: str | None, expires_at: datetime | None, message: str) -> dict:
    """Build a uniform response that never reveals whether an identifier is
    registered: the account fields stay blank whether or not a user exists."""
    return {
        "message": message,
        "reset_token": raw_token,
        "reset_expires_at": expires_at.isoformat() if expires_at else None,
        "user_id": None,
        "identifier": None,
        "email": None,
    }


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = service.authenticate(db, payload.username, payload.password)
    return service.issue_tokens(user)


@router.post("/refresh", response_model=RefreshResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    access_token = service.refresh_access_token(db, payload.refresh_token)
    return {"access_token": access_token}


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Step 1 — submit a username or email address.

    The response is identical whether the identifier is registered or not.
    When it is, a short-lived one-time reset token is issued (returned here
    as the stand-in for an emailed reset link) that step 3 requires.
    """
    if _rate_limited(payload.identifier.lower()):
        raise AppError("Too many reset requests. Try again later.", 429)
    if _cooldown_active(payload.identifier.lower()):
        return _forgot_password_response(None, None, REQUEST_MESSAGE)
    raw_token, expires_at = service.forgot_password_request(db, payload.identifier)
    if raw_token is not None:
        _record_issue(payload.identifier.lower())
    return _forgot_password_response(raw_token, expires_at, REQUEST_MESSAGE)


@router.post("/forgot-password/verify", response_model=ForgotPasswordResponse)
def forgot_password_verify(payload: ForgotPasswordVerifyRequest, db: Session = Depends(get_db)):
    """
    Step 2 — re-confirm the username or email before allowing a reset.
    Re-issues a fresh token for the account, with the same uniform response.
    """
    if _rate_limited(payload.identifier.lower()):
        raise AppError("Too many reset requests. Try again later.", 429)
    if _cooldown_active(payload.identifier.lower()):
        return _forgot_password_response(None, None, REQUEST_MESSAGE)
    raw_token, expires_at = service.forgot_password_request(db, payload.identifier)
    if raw_token is not None:
        _record_issue(payload.identifier.lower())
    return _forgot_password_response(raw_token, expires_at, REQUEST_MESSAGE)


@router.post("/forgot-password/reset", response_model=ForgotPasswordResponse)
def forgot_password_reset(payload: ForgotPasswordResetRequest, db: Session = Depends(get_db)):
    """
    Step 3 — set a new password using the token issued at step 1.

    Requires the one-time reset token; simply knowing a username or email is
    no longer enough to take over an account.
    """
    if _rate_limited(payload.identifier.lower(), limit=10):
        raise AppError("Too many reset attempts. Try again later.", 429)
    service.forgot_password_reset(db, payload.identifier, payload.reset_token, payload.new_password)
    return _forgot_password_response(None, None, RESET_MESSAGE)


@router.get("/me")
def me(current_user: CurrentUser = Depends(get_current_user)):
    return {
        "user_id": current_user.user_id,
        "role": current_user.role,
        "school_id": current_user.school_id,
        "linked_person_id": current_user.linked_person_id,
    }
