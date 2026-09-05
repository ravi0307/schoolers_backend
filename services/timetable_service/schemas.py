from datetime import datetime, time
from typing import Literal

from pydantic import BaseModel, model_validator


class TimetableEntryRead(BaseModel):
    entry_id: int
    school_id: int
    class_id: int
    day_of_week: str
    period_id: int
    period_start_time: time | None
    period_end_time: time | None
    subject_id: int | None
    teacher_id: int | None
    created_on: datetime | None
    created_by: int | None
    is_holiday_override: bool

    class Config:
        from_attributes = True


class TimetableEntryUpdate(BaseModel):
    subject_id: int | None = None
    teacher_id: int | None = None
    period_start_time: time | None = None
    period_end_time: time | None = None

    @model_validator(mode="after")
    def validate_update_fields(self):
        if all(
            value is None
            for value in (
                self.subject_id,
                self.teacher_id,
                self.period_start_time,
                self.period_end_time,
            )
        ):
            raise ValueError("At least one timetable entry field is required")
        return self


class TimetableWeekCreate(BaseModel):
    period_time: str
    subject_id: int | None = None
    teacher_id: int | None = None
    day_of_week: Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] | None = None
