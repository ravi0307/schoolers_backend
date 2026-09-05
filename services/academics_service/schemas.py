from datetime import time

from pydantic import BaseModel


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
