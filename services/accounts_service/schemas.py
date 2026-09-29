"""Schemas for the accounts service.

`amount` is a float in the API even though the column is Numeric(12,2): money
stays exact in the database, and the admin grid only ever adds these for a
display total. Callers that need exact arithmetic should read the database.
"""
import re
from datetime import date

from pydantic import BaseModel, Field, field_validator

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _validate_month(value: str) -> str:
    if not MONTH_RE.match(value):
        raise ValueError("month must be formatted YYYY-MM, e.g. 2026-09")
    return value


class SalaryCreate(BaseModel):
    staff_id: int
    month: str
    amount: float = Field(ge=0)
    paid_on: date | None = None
    note: str | None = Field(default=None, max_length=200)

    @field_validator("month")
    @classmethod
    def check_month(cls, v):
        return _validate_month(v)


class FeeCreate(BaseModel):
    student_id: int
    month: str
    amount: float = Field(ge=0)
    paid_on: date | None = None
    note: str | None = Field(default=None, max_length=200)

    @field_validator("month")
    @classmethod
    def check_month(cls, v):
        return _validate_month(v)


class SalaryEntry(BaseModel):
    """One recorded payment, as stored."""

    staff_id: int
    month: str
    amount: float
    paid_on: date | None = None
    note: str | None = None


class FeeEntry(BaseModel):
    student_id: int
    month: str
    amount: float
    paid_on: date | None = None
    note: str | None = None


class SalaryRow(BaseModel):
    """One staff member across the whole window.

    `amounts` is keyed by 'YYYY-MM' and only carries the months that have a
    payment; the client treats a missing key as unpaid rather than zero,
    because "nothing recorded" and "recorded as zero" are different facts.
    `paid_on` is the parallel map for the same months -- the date each figure
    was paid -- so the grid can show "when" next to "how much". A month with
    an amount but no recorded date carries None, never a made-up date.
    """

    staff_id: int
    staff_name: str
    designation: str | None = None
    amounts: dict[str, float]
    paid_on: dict[str, str | None] = Field(default_factory=dict)
    notes: dict[str, str | None] = Field(default_factory=dict)


class FeeRow(BaseModel):
    student_id: int
    student_name: str
    admission_no: str | None = None
    class_name: str | None = None
    amounts: dict[str, float]
    paid_on: dict[str, str | None] = Field(default_factory=dict)


class SalarySheet(BaseModel):
    months: list[str]
    rows: list[SalaryRow]
    total_paid: float
    total_outstanding_months: int


class FeeSheet(BaseModel):
    months: list[str]
    rows: list[FeeRow]
    total_collected: float
    outstanding_count: int
