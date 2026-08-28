from pydantic import BaseModel


class SchoolOverview(BaseModel):
    school_id: int
    teachers: int
    staff: int
    students: int
    parents: int
    classes: int
    pending_leave_requests: int
    active_routes: int


class ClassAttendanceTrendPoint(BaseModel):
    date: str
    present: int
    absent: int
