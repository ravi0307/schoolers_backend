from pydantic import BaseModel, Field


class MarkUpsert(BaseModel):
    term: str = "Term 1"
    score: int = Field(ge=0, le=100)


class MarkRead(BaseModel):
    mark_id: int
    student_id: int
    subject_id: int
    term: str
    score: int
    updated_by: int | None = None
    updated_by_user: int | None = None

    class Config:
        from_attributes = True


# ---- Session marks lifecycle (Phase 4) ----


class SessionMarkCreate(BaseModel):
    session_id: int
    student_id: int
    subject_id: int
    score: int | None = None
    grade_label: str | None = None
    assessment_status: str = "present"
    term: str | None = None


class SessionMarkUpdate(BaseModel):
    score: int | None = None
    grade_label: str | None = None
    assessment_status: str | None = None
    subject_id: int | None = None
    term: str | None = None


class WithholdPayload(BaseModel):
    mark_ids: list[int]
    reason: str


class UnwithholdPayload(BaseModel):
    mark_ids: list[int]


class CorrectMarkPayload(BaseModel):
    score: int | None = None
    grade_label: str | None = None
    assessment_status: str | None = None
    term: str | None = None
    reason: str


class MarkReadV2(BaseModel):
    """Session-aware mark read. Legacy rows carry session fields as null."""

    mark_id: int
    student_id: int
    subject_id: int
    term: str
    score: int | None = None
    max_marks: int | None = None
    grade_level_id: int | None = None
    result_format: str = "marks"
    workflow_status: str = "draft"
    assessment_status: str = "present"
    is_withheld: bool = False
    examination_session_id: int | None = None
    updated_by: int | None = None
    updated_by_user: int | None = None

    class Config:
        from_attributes = True


class StudentResultsRead(BaseModel):
    student_id: int
    examinations: list[dict] = []
    legacy_terms: list[dict] = []
    aggregate: dict | None = None


class ExaminationResultsRead(BaseModel):
    exam_id: int
    name: str
    status: str
    results: list[dict] = []
    missing_student_ids: list[int] = []
