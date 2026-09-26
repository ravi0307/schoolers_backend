from datetime import time

from pydantic import BaseModel, field_validator


def _clean_subject_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValueError("Subject name cannot be blank")
    if len(name) > 40:
        raise ValueError("Subject name must be 40 characters or fewer")
    return name


class SubjectCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _clean_subject_name(value)


class SubjectUpdate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _clean_subject_name(value)


class ClassCreate(BaseModel):
    name: str
    class_teacher_id: int | None = None


class ClassUpdate(BaseModel):
    name: str | None = None
    class_teacher_id: int | None = None
    student_count: int | None = None


class ClassRead(BaseModel):
    class_id: int
    school_id: int
    name: str
    class_teacher_id: int | None
    student_count: int

    class Config:
        from_attributes = True


class SubjectRead(BaseModel):
    subject_id: int
    name: str

    class Config:
        from_attributes = True


class PeriodRead(BaseModel):
    period_id: int
    period_no: int
    period_time: str
    period_start_time: time | None = None
    period_end_time: time | None = None

    class Config:
        from_attributes = True


class PeriodUpdate(BaseModel):
    period_time: str | None = None
    period_start_time: time | None = None
    period_end_time: time | None = None


class HolidayRead(BaseModel):
    day_of_week: str
    is_holiday: bool

    class Config:
        from_attributes = True


class HolidayUpdate(BaseModel):
    is_holiday: bool
