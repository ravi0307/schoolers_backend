from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class MarkUpsert(BaseModel):
    term: str = "Term 1"
    score: int = Field(ge=0, le=100)


class MarkRead(BaseModel):
    mark_id: int
    student_id: int
    subject_id: int
    term: str
    # Session marks may be published with no numeric score (absent/missing
    # rows, grade-format rows) — parents must still receive them instead
    # of a 500, so score and friends are optional and defaulted.
    score: int | None = None
    max_marks: int | None = None
    assessment_status: str | None = None
    grade_level_id: int | None = None
    updated_by: int | None = None
    updated_by_user: int | None = None
    # Read-side display metadata: subject name from the school catalog and
    # the resolved grade from the school's configured scale (None when the
    # scale defines nothing — thresholds are never invented here).
    subject_name: str | None = None
    grade: dict | None = None

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
    # Mandatory: the audit trail is meaningless without a stated reason.
    # Empty or whitespace-only strings are rejected at the HTTP boundary.
    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def _reason_must_not_be_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("A correction reason is required")
        return v


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


class CorrectionRead(BaseModel):
    """One post-publication correction audit row."""

    correction_id: int
    old_score: int | None = None
    new_score: int | None = None
    old_grade: str | None = None
    new_grade: str | None = None
    reason: str
    corrected_by: int | None = None
    corrected_by_name: str | None = None
    corrected_at: datetime | None = None
    approved_by: int | None = None
    approved_by_name: str | None = None
    approved_at: datetime | None = None
