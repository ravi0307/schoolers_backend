from datetime import date
from pydantic import BaseModel


class AttendanceMarkOne(BaseModel):
    student_id: int
    status: str  # 'Present' | 'Absent'


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
