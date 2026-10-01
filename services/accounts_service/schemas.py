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


class FeeDeposit(BaseModel):
    """One fee payment covering a plan's worth of months.

    `amount` is the TOTAL handed over, not a monthly rate: a parent gives the
    office one cheque for the quarter, and that is the number that exists. The
    service splits it across the months and reports back what each month gets,
    so the ledger never has to guess.
    """

    student_id: int
    start_month: str
    # Not a Literal: the plan table lives with the arithmetic in the repository,
    # and a plan this schema does not know about is rejected there with the
    # list of valid ones rather than as an opaque enum error.
    plan: str
    amount: float = Field(ge=0)
    paid_on: date | None = None
    note: str | None = Field(default=None, max_length=200)

    @field_validator("start_month")
    @classmethod
    def check_start_month(cls, v):
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
    notes: dict[str, str | None] = Field(default_factory=dict)


class FeePlan(BaseModel):
    """One selectable deposit period, in months."""

    plan: str
    months: int
    label: str


class FeePlans(BaseModel):
    plans: list[FeePlan]


class DepositedMonth(BaseModel):
    """One month a deposit lands on, and what it will carry."""

    month: str
    amount: float
    # True when the month already had an entry and this deposit replaces it.
    replaced: bool


class FeeDepositEntry(BaseModel):
    """What a deposit wrote, month by month.

    Returned by both the preview and the write, so what the admin was shown
    before committing is the same shape as what actually happened.
    """

    student_id: int
    start_month: str
    plan: str
    total: float
    months: list[DepositedMonth]
    created: int
    replaced: int


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
