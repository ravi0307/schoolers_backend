from datetime import date, time

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


