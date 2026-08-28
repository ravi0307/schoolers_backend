from datetime import date
from pydantic import BaseModel


class LeaveRequestCreate(BaseModel):
    requester_type: str  # 'Teacher' | 'Student' | 'Staff' | 'Pilot'
    requester_name: str
    from_date: date
    to_date: date
    reason: str | None = None


class LeaveRequestRead(BaseModel):
    leave_id: int
    school_id: int
    requester_type: str
    requester_name: str
    from_date: date
    to_date: date
    reason: str | None
    status: str

    class Config:
        from_attributes = True
