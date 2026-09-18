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
