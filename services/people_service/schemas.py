from datetime import date
from pydantic import AliasChoices, BaseModel, Field


class TeacherCreate(BaseModel):
    name: str
    role_title: str
    phone: str
    email: str | None = Field(
        default=None,
        validation_alias=AliasChoices("email", "email_id"),
    )
    present_address: str | None = None
    permanent_address: str | None = None
    date_of_birth: date | None = None
    emergency_number: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "emergency_number",
            "emergency_contact_number",
            "emergency_contact",
        ),
    )
    gender: str | None = None


class TeacherUpdate(BaseModel):
    name: str | None = None
    role_title: str | None = None
    phone: str | None = None
    email: str | None = Field(
        default=None,
        validation_alias=AliasChoices("email", "email_id"),
    )
    present_address: str | None = None
    permanent_address: str | None = None
    date_of_birth: date | None = None
    emergency_number: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "emergency_number",
            "emergency_contact_number",
            "emergency_contact",
        ),
    )
    gender: str | None = None
    attendance_status: str | None = None


class TeacherRead(BaseModel):
    teacher_id: int
    staff_id: int | None
    school_id: int
    name: str
    role_title: str
    phone: str
    email: str | None
    present_address: str | None
    permanent_address: str | None
    date_of_birth: date | None
    emergency_number: str | None
    gender: str | None
    attendance_status: str

    class Config:
        from_attributes = True


class StaffCreate(BaseModel):
    name: str
    role: str = Field(
        min_length=1,
        validation_alias=AliasChoices("role", "role_title"),
    )
    phone: str
    email: str = Field(
        min_length=1,
        validation_alias=AliasChoices("email", "email_id"),
    )
    date_of_birth: date
    marital_status: str = Field(min_length=1)
    gender: str = Field(min_length=1)
    present_address: str
    permanent_address: str
    aadhaar_card: str = Field(
        min_length=1,
        validation_alias=AliasChoices(
            "aadhaar_card",
            "aadhaar_number",
            "aadhaar_card_number",
        ),
    )
    emergency_number: str = Field(
        min_length=1,
        validation_alias=AliasChoices(
            "emergency_number",
            "emergency_contact_number",
            "emergency_contact",
        ),
    )
    driving_license: str | None = Field(default=None, min_length=1)


class StaffUpdate(BaseModel):
    name: str | None = None
    role: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("role", "role_title"),
    )
    phone: str | None = None
    email: str | None = Field(
        default=None,
        validation_alias=AliasChoices("email", "email_id"),
    )
    date_of_birth: date | None = None
    marital_status: str | None = Field(default=None, min_length=1)
    gender: str | None = Field(default=None, min_length=1)
    present_address: str | None = None
    permanent_address: str | None = None
    aadhaar_card: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "aadhaar_card",
            "aadhaar_number",
            "aadhaar_card_number",
        ),
    )
    emergency_number: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "emergency_number",
            "emergency_contact_number",
            "emergency_contact",
        ),
    )
    driving_license: str | None = Field(default=None, min_length=1)
    is_active: bool | None = None


class StaffRead(BaseModel):
    staff_id: int
    school_id: int
    name: str
    role: str
    phone: str | None
    email: str | None
    date_of_birth: date | None
    marital_status: str | None
    gender: str | None
    present_address: str | None
    permanent_address: str | None
    aadhaar_card: str | None
    emergency_number: str | None
    driving_license: str | None
    is_active: bool

    class Config:
        from_attributes = True


class StaffWithCredentials(StaffRead):
    """StaffRead plus the freshly generated portal login, when one is created."""

    admin_username: str | None = None
    admin_password: str | None = None


class ParentCreate(BaseModel):
    name: str
    phone: str
    email: str | None = None


class ParentRead(BaseModel):
    parent_id: int
    school_id: int
    name: str
    phone: str
    email: str | None

    class Config:
        from_attributes = True


class StudentCreate(BaseModel):
    class_id: int
    admission_no: str | None = None
    name: str
    date_of_birth: date | None = None
    gender: str | None = None
    photo_url: str | None = None
    aadhaar_number: str | None = None
    birth_certificate_number: str | None = None
    documents: list[str] | None = None
    parent_id: int | None = None  # optionally link on create
    parent_name: str | None = None
    parent_phone: str | None = None
    parent_email: str | None = None
    parent_address: str | None = None
    parent_emergency_number: str | None = None


class StudentUpdate(BaseModel):
    class_id: int | None = None
    admission_no: str | None = None
    name: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    photo_url: str | None = None
    aadhaar_number: str | None = None
    birth_certificate_number: str | None = None
    documents: list[str] | None = None
    present_today: bool | None = None
    parent_id: int | None = None
    parent_name: str | None = None
    parent_phone: str | None = None
    parent_email: str | None = None
    parent_address: str | None = None
    parent_emergency_number: str | None = None


class StudentRead(BaseModel):
    student_id: int
    school_id: int
    class_id: int
    admission_no: str
    name: str
    date_of_birth: date | None
    gender: str | None
    photo_url: str | None = None
    aadhaar_number: str | None = None
    birth_certificate_number: str | None = None
    documents: list[str] | None = None
    present_today: bool
    parent_id: int | None = None
    parent_name: str | None = None
    parent_phone: str | None = None
    parent_email: str | None = None
    parent_address: str | None = None
    parent_emergency_number: str | None = None

    class Config:
        from_attributes = True


class TeacherClassSubjectCreate(BaseModel):
    teacher_id: int
    class_id: int
    subject_id: int
    is_class_teacher: bool = False
