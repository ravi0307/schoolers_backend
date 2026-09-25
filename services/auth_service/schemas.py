from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from common.security import validate_password_byte_length


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    role: str
    school_id: int | None = None
    user_id: int
    linked_person_id: int | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ForgotPasswordIdentifierRequest(BaseModel):
    """Accept one account identifier: a username or email address."""
    identifier: str | None = Field(default=None, min_length=1, max_length=120)
    email: str | None = Field(default=None, min_length=1, max_length=120)
    username: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def use_one_identifier(self):
        values = [value.strip() for value in (self.identifier, self.email, self.username) if value and value.strip()]
        if len(values) != 1:
            raise ValueError("Provide exactly one of identifier, email, or username.")
        self.identifier = values[0]
        return self


class ForgotPasswordRequest(ForgotPasswordIdentifierRequest):
    """Step 1: Look up an account by email address or username."""


class ForgotPasswordVerifyRequest(ForgotPasswordIdentifierRequest):
    """Step 2: Verify an email address or username before reset."""


class ForgotPasswordResetRequest(ForgotPasswordIdentifierRequest):
    """Step 3: Submit a new password for the verified account."""
    otp: str = Field(min_length=6, max_length=6)
    new_password: str = Field(min_length=8, max_length=72)

    @field_validator("otp")
    @classmethod
    def otp_must_be_six_digits(cls, value: str) -> str:
        if not value.isdigit():
            raise ValueError("OTP must be a 6-digit code")
        return value

    @field_validator("new_password")
    @classmethod
    def new_password_within_bcrypt_limit(cls, value: str) -> str:
        validate_password_byte_length(value)
        return value


class ForgotPasswordResponse(BaseModel):
    message: str
    reset_token: str | None = None
    reset_expires_at: datetime | None = None
    user_id: int | None = None
    identifier: str | None = None
    email: str | None = None
