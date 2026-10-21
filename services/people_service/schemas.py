from datetime import date
from typing import Literal

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


class TeacherRead(BaseModel):
    """A teaching staff member.

    teacher_id is a read-only alias of staff_id: the teachers table is gone and
    every teacher is a staff row with person_type='teacher'.
    """

    teacher_id: int
    staff_id: int
    school_id: int
    name: str
    role: str
    role_title: str | None = None
    person_type: str
    phone: str
    email: str | None
    present_address: str | None
    permanent_address: str | None
    date_of_birth: date | None
    emergency_number: str | None
    gender: str | None
    is_active: bool

    class Config:
        from_attributes = True


class StaffCreate(BaseModel):
    name: str
    role: str = Field(
        min_length=1,
        validation_alias=AliasChoices("role", "role_title"),
    )
    # Employment category used by teaching assignments and reporting. When the
    # client omits it, repository.create_staff infers it from the role label
    # (so a staff-form "Teacher"/"Pilot" becomes person_type teacher/pilot and
    # reaches the teachers list and the driver sync respectively).
    person_type: Literal["teacher", "pilot", "admin", "staff"] | None = None
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
    role_title: str | None = None
    person_type: Literal["teacher", "pilot", "admin", "staff"] | None = None
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
    role_title: str | None = None
    person_type: str
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
    # staff_id is the stored key; teacher_id is accepted as an alias so existing
    # clients keep working now that the teachers table is gone.
    staff_id: int = Field(validation_alias=AliasChoices("staff_id", "teacher_id"))
    class_id: int
    subject_id: int
    is_class_teacher: bool = False


# ---- Promotion and carry-forward (Phase 5) ----


class ClassMapEntry(BaseModel):
    from_class_id: int
    to_class_id: int


class PromotionPreview(BaseModel):
    source_year_id: int | None = None
    target_year_id: int
    class_map: list[ClassMapEntry] = []


class PromotionProposedRow(BaseModel):
    student_id: int
    student_name: str
    from_class_id: int | None
    to_class_id: int | None
    suggested_status: str


class PromotionPreviewResult(BaseModel):
    proposed: list[PromotionProposedRow]
    exceptions: list[dict]
    already_done: list[int]


class PromotionAssignment(BaseModel):
    student_id: int
    to_class_id: int | None = None
    status: Literal["promoted", "retained", "transferred", "withdrawn", "new", "enrolled"]


class PromotionCommit(BaseModel):
    source_year_id: int | None = None
    target_year_id: int
    assignments: list[PromotionAssignment]
    idempotency_key: str


class PromotionBatchRead(BaseModel):
    batch_id: int
    school_id: int
    source_year_id: int | None
    target_year_id: int
    status: str
    applied: int = 0

    class Config:
        from_attributes = True


# ---- Student subject enrollment reads/writes (Phase 5) ----
# Shapes live here because the routes live under /students (people service);
# the storage functions stay in the academics repository next to offerings.


class StudentSubjectEnroll(BaseModel):
    subject_id: int
    year_id: int
    is_optional: bool = False


class StudentSubjectUpdate(BaseModel):
    state: Literal["enrolled", "dropped"]


class StudentSubjectRead(BaseModel):
    student_id: int
    subject_id: int
    subject_name: str | None = None
    academic_year_id: int
    state: str
    is_optional: bool

    class Config:
        from_attributes = True
