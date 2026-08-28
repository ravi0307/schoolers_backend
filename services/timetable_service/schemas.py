from pydantic import BaseModel


class TimetableEntryRead(BaseModel):
    entry_id: int
    class_id: int
    day_of_week: str
    period_id: int
    subject_id: int | None
    teacher_id: int | None
    is_holiday_override: bool

    class Config:
        from_attributes = True


class TimetableEntryUpdate(BaseModel):
    subject_id: int | None = None
    teacher_id: int | None = None
