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


class ChangePasswordRequest(BaseModel):
    """Self-service password change for a signed-in user.

    Distinct from the forgot-password flow: that one is anonymous and gated on
    an emailed OTP, while this one requires the caller to already hold a valid
    access token *and* to re-enter their current password, so it can never be
    used to take over an account whose session token was stolen.
    """
    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=8, max_length=72)

    @field_validator("new_password")
    @classmethod
    def new_password_within_bcrypt_limit(cls, value: str) -> str:
        validate_password_byte_length(value)
        return value

    @model_validator(mode="after")
    def new_password_must_differ(self):
        if self.current_password == self.new_password:
            raise ValueError("Choose a new password that is different from your current one.")
        return self


class ChangePasswordResponse(BaseModel):
    message: str


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


# ---- Privileged accounts: the Master-managed Sub-Master lifecycle ----


class PrivilegedAccountCreate(BaseModel):
    """Master's payload to create a Sub-Master account.

    Deliberately has no `role` field: every account minted through this
    endpoint is a Sub-Master by construction, so no payload can make it mint
    a Master (or any other role). The password is optional — omit it and one
    is generated — and is returned exactly once, with the creation response,
    for the Master to hand over.
    """
    username: str = Field(min_length=3, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=72)

    @field_validator("username")
    @classmethod
    def username_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Username is required")
        return value

    @field_validator("email")
    @classmethod
    def email_trimmed(cls, value: str | None) -> str | None:
        return value.strip() if value else value

    @field_validator("password")
    @classmethod
    def password_within_bcrypt_limit(cls, value: str | None) -> str | None:
        if value:
            validate_password_byte_length(value)
        return value


class PrivilegedAccountUpdate(BaseModel):
    """Partial update of a Sub-Master; a field left None means unchanged.

    An empty `email` clears the address; `is_active` flips activate /
    deactivate; `password` rotates the credential (and clears any outstanding
    reset OTP so a code issued before the change cannot be redeemed).
    """
    email: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=72)

    @field_validator("email")
    @classmethod
    def email_trimmed(cls, value: str | None) -> str | None:
        return value.strip() if value else value

    @field_validator("password")
    @classmethod
    def password_within_bcrypt_limit(cls, value: str | None) -> str | None:
        if value:
            validate_password_byte_length(value)
        return value


class PrivilegedAccountRead(BaseModel):
    user_id: int
    username: str
    role: str
    email: str | None = None
    is_active: bool
    created_at: datetime | None = None


class PrivilegedAccountCreateResponse(PrivilegedAccountRead):
    """Creation view: includes the plaintext password shown once, at creation.

    Only its bcrypt hash is stored; the value is never retrievable again from
    the API (a Master who loses it rotates the credential via PATCH).
    """
    password: str
