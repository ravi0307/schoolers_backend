from enum import Enum


class Role(str, Enum):
    parent = "parent"
    teacher = "teacher"
    admin = "admin"
    pilot = "pilot"
    master = "master"


class SchoolStatus(str, Enum):
    active = "Active"
    inactive = "Inactive"


class AttendanceStatus(str, Enum):
    present = "Present"
    absent = "Absent"


class LeaveStatus(str, Enum):
    pending = "Pending"
    approved = "Approved"
    rejected = "Rejected"


class LeaveRequesterType(str, Enum):
    teacher = "Teacher"
    student = "Student"
    staff = "Staff"
    pilot = "Pilot"


class BroadcastScope(str, Enum):
    school = "school"
    class_ = "class"
    pilot = "pilot"


class NotificationType(str, Enum):
    dues = "Dues"
    activation = "Activation"
    general = "General"


class RouteStatus(str, Enum):
    on_route = "On route"
    scheduled = "Scheduled"
    inactive = "Inactive"


class StopType(str, Enum):
    pickup = "pickup"
    drop = "drop"
