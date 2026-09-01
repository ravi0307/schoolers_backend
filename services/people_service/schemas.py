from datetime import date
from pydantic import AliasChoices, BaseModel, Field


class TeacherCreate(BaseModel):
    name: str
    role_title: str
    phone: str
    email: str | None = None


class TeacherUpdate(BaseModel):
    name: str | None = None
    role_title: str | None = None
    phone: str | None = None
    email: str | None = None
    attendance_status: str | None = None


class TeacherRead(BaseModel):
    teacher_id: int
    school_id: int
    name: str
    role_title: str
    phone: str
    email: str | None
    attendance_status: str

    class Config:
        from_attributes = True


class StaffCreate(BaseModel):
    name: str
    role: str = Field(validation_alias=AliasChoices("role", "role_title"))
    phone: str | None = None
    email: str | None = Field(
        default=None,
        validation_alias=AliasChoices("email", "email_id"),
    )
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


class StaffUpdate(BaseModel):
    name: str | None = None
    role: str | None = Field(
        default=None,
        validation_alias=AliasChoices("role", "role_title"),
    )
    phone: str | None = None
    email: str | None = Field(
        default=None,
        validation_alias=AliasChoices("email", "email_id"),
    )
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


class StaffRead(BaseModel):
    staff_id: int
    school_id: int
    name: str
    role: str
    phone: str | None
    email: str | None
    present_address: str | None
    permanent_address: str | None
    aadhaar_card: str | None
    emergency_number: str | None

    class Config:
        from_attributes = True


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
