from pydantic import BaseModel, Field, model_validator


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
    """Step 1: Look up an account by email address or username and send an OTP."""


class ForgotPasswordResetRequest(ForgotPasswordIdentifierRequest):
    """Step 2: Submit the 6-digit OTP received by email plus a new password."""
    otp: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")
    new_password: str = Field(min_length=8, max_length=72)


class ForgotPasswordResponse(BaseModel):
    message: str
    user_id: int | None = None
    identifier: str | None = None
    email: str | None = None
