from typing import Literal

from pydantic import AliasChoices, BaseModel, Field


class SchoolBase(BaseModel):
    name: str
    address: str
    pincode: str
    city: str
    state: str
    country: str = "India"
    primary_contact: str
    alternative_contact: str | None = Field(
        default=None,
        validation_alias=AliasChoices("alternative_contact", "alternate_contact"),
    )
    primary_email: str
    alternative_email: str | None = Field(
        default=None,
        validation_alias=AliasChoices("alternative_email", "alternate_email"),
    )
    first_name: str | None = None
    last_name: str | None = None
    logo_url: str | None = None


class SchoolCreate(SchoolBase):
    pass


class SchoolUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    pincode: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    primary_contact: str | None = None
    alternative_contact: str | None = Field(
        default=None,
        validation_alias=AliasChoices("alternative_contact", "alternate_contact"),
    )
    primary_email: str | None = None
    alternative_email: str | None = Field(
        default=None,
        validation_alias=AliasChoices("alternative_email", "alternate_email"),
    )
    first_name: str | None = None
    last_name: str | None = None
    logo_url: str | None = None


class FeatureFlags(BaseModel):
    route_enabled: bool | None = None
    website_enabled: bool | None = None
    library_enabled: bool | None = None
    fees_enabled: bool | None = None
    salary_enabled: bool | None = None
    examinations_enabled: bool | None = None


class SchoolStatusUpdate(BaseModel):
    status: str  # 'Active' | 'Inactive'


class SchoolRead(SchoolBase):
    school_id: int
    status: str
    marking_system: str = "marks"
    examinations_enabled: bool = False
    route_enabled: bool
    website_enabled: bool
    library_enabled: bool
    fees_enabled: bool
    salary_enabled: bool

    class Config:
        from_attributes = True


class SchoolWithCredentials(SchoolRead):
    """SchoolRead plus the freshly-issued admin login credentials.

    Returned only when a school admin account is provisioned/rotated by the
    create/update endpoints; every other school endpoint stays on SchoolRead.
    """

    admin_username: str | None = None
    admin_password: str | None = None


class SchoolStats(BaseModel):
    teachers: int
    staff: int
    students: int
    parents: int

class MarkingSystemUpdate(BaseModel):
    """Switch the school's marking system.

    `confirm` is required when the school already has marks or published
    examinations: switching only affects NEW examinations and entries
    (published history keeps its captured format), and the confirmation
    proves the admin saw the impact counts from the 409 response.
    """

    marking_system: Literal["marks", "grades"]
    confirm: bool = False
