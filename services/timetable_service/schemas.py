from datetime import date, datetime, time
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
    #: Marks this period as deliberately scheduled on a holiday ("extra class").
    #: Explicit because timetable entries are a recurring weekday template with
    #: no date of their own, so a dated holiday list cannot tell us whether this
    #: particular occurrence is a holiday day. Omit to leave the flag as it is.
    is_holiday_override: bool | None = None

    @model_validator(mode="after")
    def validate_update_fields(self):
        if all(
            value is None
            for value in (
                self.subject_id,
                self.teacher_id,
                self.period_start_time,
                self.period_end_time,
                self.is_holiday_override,
            )
        ):
            raise ValueError("At least one timetable entry field is required")
        return self


class TimetableWeekCreate(BaseModel):
    period_time: str
    subject_id: int | None = None
    teacher_id: int | None = None
    day_of_week: Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] | None = None


class TimetableDayRead(BaseModel):
    """One column of the weekly grid, resolved against a real calendar week."""

    day_of_week: str
    date: date
    is_holiday: bool
    #: The holiday's name, so the column can say *why* it is red rather than
    #: just "HOLIDAY". None on an ordinary day.
    holiday_name: str | None = None
    entries: list[TimetableEntryRead]


class TimetableWeekRead(BaseModel):
    """A class timetable anchored to a calendar week.

    Entries stay a recurring weekday template, so the same subject/period rows
    come back for every week; ``date`` and ``is_holiday`` are what vary, and
    they are resolved per week so the UI can label and highlight columns.
    """

    class_id: int
    school_id: int
    week_start: date
    week_end: date
    days: list[TimetableDayRead]
