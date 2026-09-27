from datetime import date, time
from typing import Literal

from pydantic import BaseModel


class AttendanceMarkOne(BaseModel):
    student_id: int
    status: Literal["Present", "Absent"]


class AttendanceMarkBulk(BaseModel):
    class_id: int
    date: date
    entries: list[AttendanceMarkOne]


class AttendanceRead(BaseModel):
    attendance_id: int
    student_id: int
    class_id: int
    date: date
    status: str

    class Config:
        from_attributes = True


class ClassAttendanceSummary(BaseModel):
    class_id: int
    date: date
    present: int
    absent: int
    total: int


class StaffAttendanceMarkOne(BaseModel):
    staff_id: int
    status: Literal["Present", "Absent", "On leave", "Half day"]
    check_in: str | None = None
    check_out: str | None = None
    remarks: str | None = None


class StaffAttendanceMarkBulk(BaseModel):
    date: date
    entries: list[StaffAttendanceMarkOne]


class StaffAttendanceRead(BaseModel):
    attendance_id: int
    school_id: int
    staff_id: int
    staff_name: str | None = None
    person_type: str | None = None
    date: date
    status: str
    check_in: time | None = None
    check_out: time | None = None
    remarks: str | None = None

    class Config:
        from_attributes = True
