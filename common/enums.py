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
    route = "route"
    pilot = "pilot"


class NotificationType(str, Enum):
    dues = "Dues"
    activation = "Activation"
    general = "General"


class TicketStatus(str, Enum):
    open = "Open"
    in_progress = "In progress"
    assigned = "Assigned"
    completed = "Completed"
    cancelled = "Cancelled"


class RouteStatus(str, Enum):
    on_route = "On route"
    scheduled = "Scheduled"
    inactive = "Inactive"


class StopType(str, Enum):
    pickup = "pickup"
    drop = "drop"


class TripDirection(str, Enum):
    """The leg of a route run a trip instance covers. Uses the transport
    domain's own vocabulary (RouteStop.stop_type / pickup_time / drop_time):
    a day has a boarding run (pickup) and a returning run (drop). The draft's
    morning/evening was dropped because nothing in the app establishes that
    mapping."""

    pickup = "pickup"
    drop = "drop"


class TripStatus(str, Enum):
    """Lifecycle of one trip instance. Values are constrained by a CHECK; the
    permitted transitions (scheduled -> in_progress -> completed, and
    cancel/reopen from in_progress) are enforced by the future trip service,
    not by the schema."""

    scheduled = "scheduled"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


class BoardingStatus(str, Enum):
    """Per-student outcome of the boarding (pickup) leg. 'picked' is the term
    the live route model already uses (RouteStudent.status), kept here so trip
    history and parent-facing snapshots speak the same language."""

    pending = "pending"
    picked = "picked"
    did_not_board = "did_not_board"


class DropStatus(str, Enum):
    """Per-student outcome of the drop leg. 'dropped' matches the live model;
    'drop_not_recorded' covers a pick-up where the driver never logged a
    drop."""

    pending = "pending"
    dropped = "dropped"
    drop_not_recorded = "drop_not_recorded"
