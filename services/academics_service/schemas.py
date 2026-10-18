from datetime import date, time
# `dt` alias: annotations evaluate lazily, so a field literally named `date`
# would shadow the `date` type in its own annotation. New session schemas
# use `dt.date` / `dt.time` for that reason.
import datetime as dt
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator


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
    class_teacher_staff_id: int | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "class_teacher_staff_id", "class_teacher_id"
        ),
    )


class ClassUpdate(BaseModel):
    name: str | None = None
    class_teacher_staff_id: int | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "class_teacher_staff_id", "class_teacher_id"
        ),
    )
    student_count: int | None = None


class ClassRead(BaseModel):
    """class_teacher_id is a read-only alias of class_teacher_staff_id."""

    class_id: int
    school_id: int
    name: str
    class_teacher_staff_id: int | None
    class_teacher_id: int | None
    student_count: int

    class Config:
        from_attributes = True


class SubjectRead(BaseModel):
    subject_id: int
    school_id: int
    name: str
    is_active: bool

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
    holiday_id: int
    school_id: int
    occasion: str
    holiday_date: date

    class Config:
        from_attributes = True


def _clean_occasion(value: str) -> str:
    """Trim the occasion and reject anything that could not label a date.

    Without this an occasion of "  " would pass every length check and render as
    a blank cell in the admin table and as an unnamed red column in the
    timetable, which is indistinguishable from a bug.
    """
    occasion = (value or "").strip()
    if not occasion:
        raise ValueError("Occasion cannot be blank")
    if len(occasion) > 120:
        raise ValueError("Occasion must be 120 characters or fewer")
    return occasion


class HolidayCreate(BaseModel):
    """Add one named holiday, on one date or across a span of them.

    ``end_date`` is optional. Leaving it out (or matching ``holiday_date``) makes
    a single-day holiday, so a one-day entry takes the same path as a break.
    """

    occasion: str
    holiday_date: date
    end_date: date | None = None

    _validate_occasion = field_validator("occasion")(_clean_occasion)

    @model_validator(mode="after")
    def _end_is_not_before_start(self):
        if self.end_date is not None and self.end_date < self.holiday_date:
            raise ValueError(
                f"The last day ({self.end_date.isoformat()}) is before the first "
                f"day ({self.holiday_date.isoformat()})"
            )
        return self


class HolidayUpdate(BaseModel):
    """Change a holiday in place. Both fields are optional so the admin portal
    can PATCH just the date (rescheduling a holiday) or just the name.

    The edit applies to the whole run of consecutive days that share the
    occasion, so lengthening or moving a break moves all of it. ``end_date``
    widens or narrows that run from its current first day."""

    occasion: str | None = None
    holiday_date: date | None = None
    end_date: date | None = None

    @field_validator("occasion")
    @classmethod
    def _known_occasion(cls, value: str | None) -> str | None:
        return None if value is None else _clean_occasion(value)

    @model_validator(mode="after")
    def _end_is_not_before_start(self):
        # Compare against whichever first day the edit is moving the run to.
        first = self.holiday_date
        if self.end_date is not None and first is not None and self.end_date < first:
            raise ValueError(
                f"The last day ({self.end_date.isoformat()}) is before the first "
                f"day ({first.isoformat()})"
            )
        return self




def _clean_year_name(value: str) -> str:
    return _clean_calendar_name(value, "Year name", 20)


def _clean_optional_year_name(value: str | None) -> str | None:
    return None if value is None else _clean_calendar_name(value, "Year name", 20)


def _clean_period_name(value: str) -> str:
    return _clean_calendar_name(value, "Period name", 40)


def _clean_optional_period_name(value: str | None) -> str | None:
    return None if value is None else _clean_calendar_name(value, "Period name", 40)


def _clean_event_title(value: str) -> str:
    return _clean_calendar_name(value, "Event title", 150)


def _clean_optional_event_title(value: str | None) -> str | None:
    return None if value is None else _clean_calendar_name(value, "Event title", 150)


# ---- Academic calendar (Phase 1): years, periods, dated events ----

EventType = Literal["exam_period", "revision", "result_date", "other"]


def _clean_calendar_name(value: str, field: str, limit: int) -> str:
    name = value.strip()
    if not name:
        raise ValueError(f"{field} cannot be blank")
    if len(name) > limit:
        raise ValueError(f"{field} must be at most {limit} characters")
    return name


class AcademicYearCreate(BaseModel):
    name: str
    start_date: date
    end_date: date

    _validate_name = field_validator("name")(_clean_year_name)

    @model_validator(mode="after")
    def _end_after_start(self):
        if self.end_date <= self.start_date:
            raise ValueError("The year end date must be after the start date")
        return self


class AcademicYearUpdate(BaseModel):
    name: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_active: bool | None = None

    _validate_name = field_validator("name")(_clean_optional_year_name)


class AcademicYearRead(BaseModel):
    year_id: int
    school_id: int
    name: str
    start_date: date
    end_date: date
    is_active: bool
    is_founding: bool

    class Config:
        from_attributes = True


class AcademicPeriodCreate(BaseModel):
    year_id: int
    name: str
    start_date: date
    end_date: date

    _validate_name = field_validator("name")(_clean_period_name)

    @model_validator(mode="after")
    def _end_after_start(self):
        if self.end_date <= self.start_date:
            raise ValueError("The period end date must be after the start date")
        return self


class AcademicPeriodUpdate(BaseModel):
    name: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_active: bool | None = None

    _validate_name = field_validator("name")(_clean_optional_period_name)


class AcademicPeriodRead(BaseModel):
    period_id: int
    year_id: int
    name: str
    start_date: date
    end_date: date
    is_active: bool

    class Config:
        from_attributes = True


class AcademicEventCreate(BaseModel):
    year_id: int | None = None
    event_type: EventType
    title: str
    start_date: date
    end_date: date | None = None

    _validate_title = field_validator("title")(_clean_event_title)

    @model_validator(mode="after")
    def _end_not_before_start(self):
        end = self.end_date if self.end_date is not None else self.start_date
        if end < self.start_date:
            raise ValueError("The event end date cannot be before the start date")
        return self


class AcademicEventUpdate(BaseModel):
    year_id: int | None = None
    event_type: EventType | None = None
    title: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_active: bool | None = None

    _validate_title = field_validator("title")(_clean_optional_event_title)


class AcademicEventRead(BaseModel):
    event_id: int
    school_id: int
    year_id: int | None
    event_type: str
    title: str
    start_date: date
    end_date: date
    is_active: bool

    class Config:
        from_attributes = True


# ---- Examination setup (Phase 2): types, grade scales ----


def _clean_type_name(value: str) -> str:
    return _clean_calendar_name(value, "Examination type name", 60)


def _clean_optional_type_name(value: str | None) -> str | None:
    return None if value is None else _clean_calendar_name(value, "Examination type name", 60)


class ExaminationTypeCreate(BaseModel):
    name: str
    description: str | None = None
    default_max_marks: int = 100

    _validate_name = field_validator("name")(_clean_type_name)

    @model_validator(mode="after")
    def _positive_max(self):
        if self.default_max_marks <= 0:
            raise ValueError("Default maximum marks must be positive")
        return self


class ExaminationTypeUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    default_max_marks: int | None = None
    is_active: bool | None = None

    _validate_name = field_validator("name")(_clean_optional_type_name)

    @model_validator(mode="after")
    def _positive_max(self):
        if self.default_max_marks is not None and self.default_max_marks <= 0:
            raise ValueError("Default maximum marks must be positive")
        return self


class ExaminationTypeRead(BaseModel):
    type_id: int
    school_id: int
    name: str
    description: str | None
    default_max_marks: int
    is_active: bool

    class Config:
        from_attributes = True


def _clean_scale_name(value: str) -> str:
    return _clean_calendar_name(value, "Grade scale name", 60)


def _clean_optional_scale_name(value: str | None) -> str | None:
    return None if value is None else _clean_calendar_name(value, "Grade scale name", 60)


def _clean_grade_label(value: str) -> str:
    label = value.strip()
    if not label:
        raise ValueError("Grade label cannot be blank")
    if len(label) > 10:
        raise ValueError("Grade label must be at most 10 characters")
    return label


class GradeScaleCreate(BaseModel):
    name: str

    _validate_name = field_validator("name")(_clean_scale_name)


class GradeScaleUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None

    _validate_name = field_validator("name")(_clean_optional_scale_name)


class GradeScaleRead(BaseModel):
    scale_id: int
    school_id: int
    name: str
    version: int
    supersedes_id: int | None
    effective_from: date | None
    is_active: bool

    class Config:
        from_attributes = True


class GradeLevelCreate(BaseModel):
    label: str
    rank: int = 0
    min_score: int | None = None
    max_score: int | None = None
    description: str | None = None

    _validate_label = field_validator("label")(_clean_grade_label)

    @model_validator(mode="after")
    def _band_order(self):
        if (
            self.min_score is not None
            and self.max_score is not None
            and self.max_score < self.min_score
        ):
            raise ValueError("Grade max score cannot be below min score")
        return self


class GradeLevelUpdate(BaseModel):
    label: str | None = None
    rank: int | None = None
    min_score: int | None = None
    max_score: int | None = None
    description: str | None = None

    _validate_label = field_validator("label")(classmethod(
        lambda cls, v: None if v is None else _clean_grade_label(v)
    ))


class GradeLevelRead(BaseModel):
    level_id: int
    scale_id: int
    label: str
    rank: int
    min_score: int | None
    max_score: int | None
    description: str | None

    class Config:
        from_attributes = True


# ---- Examinations and sessions (Phase 3) ----

ResultFormat = Literal["marks", "grade"]
ExamStatus = Literal["draft", "published", "cancelled"]
SessionStatus = Literal["scheduled", "rescheduled", "cancelled", "completed"]


class ExaminationCreate(BaseModel):
    type_id: int
    year_id: int
    period_id: int | None = None
    name: str
    description: str | None = None
    result_format_expectation: ResultFormat = "marks"
    grade_scale_id: int | None = None

    _validate_name = field_validator("name")(classmethod(
        lambda cls, v: _clean_calendar_name(v, "Examination name", 120)
    ))

    @model_validator(mode="after")
    def _scale_matches_format(self):
        if self.result_format_expectation == "grade" and self.grade_scale_id is None:
            raise ValueError("Grade-wise examinations require a grade scale")
        if self.result_format_expectation == "marks" and self.grade_scale_id is not None:
            raise ValueError("Marks-wise examinations must not set a grade scale")
        return self


class ExaminationUpdate(BaseModel):
    """Draft-only edits. Format, year, period, and type are immutable once
    set (sessions already reference them); published examinations change
    only through publish/unpublish/cancel endpoints."""

    name: str | None = None
    description: str | None = None
    grade_scale_id: int | None = None

    _validate_name = field_validator("name")(classmethod(
        lambda cls, v: None if v is None else _clean_calendar_name(v, "Examination name", 120)
    ))


class ExaminationRead(BaseModel):
    exam_id: int
    school_id: int
    type_id: int
    year_id: int
    period_id: int | None
    name: str
    description: str | None
    result_format_expectation: str
    grade_scale_id: int | None
    status: str
    created_by: int | None

    class Config:
        from_attributes = True


class SessionCreate(BaseModel):
    class_id: int
    subject_id: int
    date: dt.date
    start_time: dt.time
    end_time: dt.time
    venue: str | None = None
    max_marks: int
    # Required when the parent examination is published (recorded on a
    # version row like amendments); draft adds ignore it.
    reason: str | None = None

    @model_validator(mode="after")
    def _time_and_max(self):
        if self.end_time <= self.start_time:
            raise ValueError("Session end time must be after start time")
        if self.max_marks <= 0:
            raise ValueError("Session maximum marks must be positive")
        return self


class SessionUpdate(BaseModel):
    """Rescheduling fields. `reason` is required when the parent examination
    is published (it lands on the version row); draft edits omit it."""

    class_id: int | None = None
    subject_id: int | None = None
    date: dt.date | None = None
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    venue: str | None = None
    max_marks: int | None = None
    reason: str | None = None


class SessionRead(BaseModel):
    session_id: int
    exam_id: int
    class_id: int
    subject_id: int
    date: dt.date
    start_time: dt.time
    end_time: dt.time
    venue: str | None
    max_marks: int
    status: str

    class Config:
        from_attributes = True


class SessionWriteResult(BaseModel):
    """A session write plus the non-blocking warnings computed for it.

    Warnings are computed, returned, and never stored: re-reading the
    timetable recomputes them from live data.
    """

    session: SessionRead
    warnings: list[str] = []


class SessionTimetableRow(SessionRead):
    exam_name: str
    exam_type_name: str
    class_name: str
    subject_name: str


class SessionVersionRead(BaseModel):
    version_id: int
    session_id: int
    changed_by: int | None
    changed_at: object | None = None
    reason: str | None
    old_row: object | None = None
    new_row: object | None = None

    class Config:
        from_attributes = True


class ExaminationCancel(BaseModel):
    reason: str

    _validate_reason = field_validator("reason")(classmethod(
        lambda cls, v: _clean_calendar_name(v, "Cancellation reason", 255)
    ))


# ---- Subject offerings and enrollment (Phase 5) ----


class ClassSubjectSet(BaseModel):
    subject_id: int
    is_required: bool = True


class ClassSubjectRead(BaseModel):
    class_id: int
    subject_id: int
    subject_name: str | None = None
    is_required: bool

    class Config:
        from_attributes = True


