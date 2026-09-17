from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import get_current_user, CurrentUser
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
    raw_token, expires_at = service.forgot_password_request(db, payload.identifier)
    return _forgot_password_response(raw_token, expires_at, REQUEST_MESSAGE)


@router.post("/forgot-password/verify", response_model=ForgotPasswordResponse)
def forgot_password_verify(payload: ForgotPasswordVerifyRequest, db: Session = Depends(get_db)):
    """
    Step 2 — re-confirm the username or email before allowing a reset.
    Re-issues a fresh token for the account, with the same uniform response.
    """
    raw_token, expires_at = service.forgot_password_request(db, payload.identifier)
    return _forgot_password_response(raw_token, expires_at, REQUEST_MESSAGE)


@router.post("/forgot-password/reset", response_model=ForgotPasswordResponse)
def forgot_password_reset(payload: ForgotPasswordResetRequest, db: Session = Depends(get_db)):
    """
    Step 3 — set a new password using the token issued at step 1.

    Requires the one-time reset token; simply knowing a username or email is
    no longer enough to take over an account.
    """
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
