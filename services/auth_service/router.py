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


def _forgot_password_response(user, message: str, identifier: str) -> dict:
    """Build a consistent response without exposing account details on misses."""
    found_identifier = identifier if user else None
    return {
        "message": message,
        "user_id": user.user_id if user else None,
        "identifier": found_identifier,
        "email": found_identifier if found_identifier and "@" in found_identifier else None,
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

    Verifies that *identifier* belongs to a registered user. In production this
    would also send a one-time reset link; here it simply confirms the
    email exists so the client can proceed to step 2.
    """
    user, message = service.forgot_password_request(db, payload.identifier)
    return _forgot_password_response(user, message, payload.identifier)


@router.post("/forgot-password/verify", response_model=ForgotPasswordResponse)
def forgot_password_verify(payload: ForgotPasswordVerifyRequest, db: Session = Depends(get_db)):
    """
    Step 2 — re-confirm the username or email before allowing a reset.
    """
    user, message = service.forgot_password_request(db, payload.identifier)
    return _forgot_password_response(user, message, payload.identifier)


@router.post("/forgot-password/reset", response_model=ForgotPasswordResponse)
def forgot_password_reset(payload: ForgotPasswordResetRequest, db: Session = Depends(get_db)):
    """
    Step 3 — set a new password for the account matching *identifier*.
    """
    user = service.forgot_password_reset(db, payload.identifier, payload.new_password)
    return _forgot_password_response(
        user,
        f"Password for user '{user.username}' has been reset.",
        payload.identifier,
    )


@router.get("/me")
def me(current_user: CurrentUser = Depends(get_current_user)):
    return {
        "user_id": current_user.user_id,
        "role": current_user.role,
        "school_id": current_user.school_id,
        "linked_person_id": current_user.linked_person_id,
    }
