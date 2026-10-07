from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from common.enums import BoardingStatus, DropStatus, TripDirection
from common.security import validate_password_byte_length


def _password_within_bcrypt_limit(value: str) -> str:
    validate_password_byte_length(value)
    return value


class VehicleCreate(BaseModel):
    vehicle_number: str
    vehicle_type: str | None = None
    registration_number: str


class VehicleUpdate(BaseModel):
    vehicle_number: str | None = None
    vehicle_type: str | None = None
    registration_number: str | None = None


class VehicleRead(BaseModel):
    vehicle_id: int
    school_id: int
    vehicle_number: str
    vehicle_type: str | None
    registration_number: str
    is_active: bool

    class Config:
        from_attributes = True


class PilotCreate(BaseModel):
    # Credentials are optional: a driver can be recorded without portal access.
    username: str | None = None
    password: str | None = None
    full_name: str
    phone: str = ""
    email: str | None = None
    present_address: str | None = None
    permanent_address: str | None = None
    aadhaar_number: str | None = None
    dl_number: str | None = None
    license_expiry: date | None = None
    route_id: int | None = None

    @field_validator("password")
    @classmethod
    def password_within_bcrypt_limit(cls, value: str) -> str:
        _password_within_bcrypt_limit(value)
        return value


class PilotUpdate(BaseModel):
    username: str | None = None
    password: str | None = None
    full_name: str | None = None
    phone: str | None = None
    email: str | None = None
    present_address: str | None = None
    permanent_address: str | None = None
    aadhaar_number: str | None = None
    dl_number: str | None = None
    license_expiry: date | None = None
    route_id: int | None = None
    is_active: bool | None = None

    @field_validator("password")
    @classmethod
    def password_within_bcrypt_limit(cls, value: str | None) -> str | None:
        if value is not None:
            _password_within_bcrypt_limit(value)
        return value


class PilotRead(BaseModel):
    pilot_id: int
    staff_id: int | None = None
    user_id: int | None = None
    school_id: int
    role: str
    username: str | None = None
    full_name: str
    email: str | None
    phone: str
    present_address: str | None
    permanent_address: str | None
    aadhaar_number: str | None
    dl_number: str | None
    license_expiry: date | None = None
    route_id: int | None = None
    is_active: bool

    class Config:
        from_attributes = True


class RouteCreate(BaseModel):
    name: str
    vehicle: str
    driver_pilot_id: int | None = None
    status: str = "Scheduled"


class RouteUpdate(BaseModel):
    name: str | None = None
    vehicle: str | None = None
    # driver_name is intentionally not writable: it is derived from the driver.
    driver_pilot_id: int | None = None
    status: str | None = None


class RouteRead(BaseModel):
    route_id: int
    school_id: int
    name: str
    vehicle: str
    # Derived from the assigned driver, so the parent portal keeps working.
    driver_name: str | None = None
    status: str

    class Config:
        from_attributes = True


class StopCreate(BaseModel):
    stop_name: str | None = None
    pickup_time: str | None = None
    pickup_order: int = 1
    drop_time: str | None = None
    drop_order: int = 1
    # Legacy single-direction fields remain accepted during the API transition.
    name: str | None = None
    stop_time: str | None = None
    stop_type: str | None = None
    stop_order: int | None = None

    @model_validator(mode="after")
    def normalize_legacy_fields(self):
        if self.stop_name is None:
            self.stop_name = self.name
        if self.stop_type == "pickup" and self.pickup_time is None:
            self.pickup_time = self.stop_time
            if self.stop_order is not None:
                self.pickup_order = self.stop_order
        if self.stop_type == "drop" and self.drop_time is None:
            self.drop_time = self.stop_time
            if self.stop_order is not None:
                self.drop_order = self.stop_order
        if not self.stop_name:
            raise ValueError("stop_name is required")
        if not self.pickup_time and not self.drop_time:
            raise ValueError("pickup_time or drop_time is required")
        return self


class StopUpdate(BaseModel):
    stop_name: str | None = None
    pickup_time: str | None = None
    pickup_order: int | None = None
    drop_time: str | None = None
    drop_order: int | None = None
    # Legacy single-direction fields remain accepted during the API transition.
    name: str | None = None
    stop_time: str | None = None
    stop_type: str | None = None
    stop_order: int | None = None

    @model_validator(mode="after")
    def normalize_legacy_fields(self):
        if self.stop_name is None:
            self.stop_name = self.name
        if self.stop_type == "pickup" and self.pickup_time is None:
            self.pickup_time = self.stop_time
            self.pickup_order = self.stop_order
        if self.stop_type == "drop" and self.drop_time is None:
            self.drop_time = self.stop_time
            self.drop_order = self.stop_order
        return self


class StopRead(BaseModel):
    stop_id: int
    route_id: int
    stop_name: str
    pickup_time: str | None
    pickup_order: int | None
    drop_time: str | None
    drop_order: int | None
    pickup_stop_id: int | None = None
    drop_stop_id: int | None = None



class RouteStudentRead(BaseModel):
    id: int
    route_id: int
    student_id: int
    student_name: str
    admission_no: str
    status: str


class RouteStudentStatusUpdate(BaseModel):
    status: str  # 'pending' | 'picked' | 'dropped'


class ParentPickDropRead(BaseModel):
    """Pick/drop snapshot for one of the logged-in parent's children."""

    student_id: int
    student_name: str
    admission_no: str
    route_id: int | None = None
    route_name: str | None = None
    vehicle: str | None = None
    driver_name: str | None = None
    status: str = "not_assigned"  # 'pending' | 'picked' | 'dropped' | 'not_assigned'
    # The route's stop schedule, in boarding order. Without this a parent sees
    # only "Route 1 / Picked up" and cannot tell where or when. Note the stops
    # belong to the route, not to the child: route_students does not record
    # which stop a given student boards at, so this is the full route stop
    # list and the client decides which one is next.
    stops: list[StopRead] = Field(default_factory=list)


class TripSummaryRead(BaseModel):
    """Base read model for a historical trip. Shared by admin, pilot and parent."""

    trip_id: int
    trip_date: date
    route_id: int
    route_name: str
    direction: str  # 'pickup' | 'drop'
    status: str  # 'scheduled' | 'in_progress' | 'completed' | 'cancelled'
    pilot_id: int | None = None
    driver_name: str | None = None
    vehicle: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    cancelled_at: datetime | None = None


class AdminTripRead(TripSummaryRead):
    """Admin-facing summary; adds the server-computed outcome summary string."""

    outcome_summary: str = ""


class TripStudentRead(BaseModel):
    """One student's boarding/drop snapshot within a trip."""

    student_id: int
    student_name: str
    boarding_status: str  # 'pending' | 'picked' | 'did_not_board'
    boarding_at: datetime | None = None
    boarding_stop_id: int | None = None
    drop_status: str  # 'pending' | 'dropped' | 'drop_not_recorded'
    drop_at: datetime | None = None
    drop_stop_id: int | None = None


class AdminTripDetailRead(AdminTripRead):
    """Full admin detail: summary + cancellation/reopen history + students."""

    cancelled_by: int | None = None
    cancellation_reason: str | None = None
    reopened_at: datetime | None = None
    reopened_by: int | None = None
    reopen_reason: str | None = None
    students: list[TripStudentRead] = Field(default_factory=list)


class ParentTripRead(TripSummaryRead):
    """A completed trip as seen by a parent: trip facts + this child's snapshot."""

    boarding_status: str  # 'pending' | 'picked' | 'did_not_board'
    boarding_at: datetime | None = None
    boarding_stop_id: int | None = None
    boarding_stop_name: str | None = None
    drop_status: str  # 'pending' | 'dropped' | 'drop_not_recorded'
    drop_at: datetime | None = None
    drop_stop_id: int | None = None
    drop_stop_name: str | None = None


class TripCreate(BaseModel):
    """Create the day's trip for a route.

    Only identity inputs come from the client. Everything else (school scope,
    pilot, driver_name, vehicle) is snapshotted server-side from the route's
    own records, never trusted from the payload.
    """

    route_id: int
    trip_date: date
    direction: str = "pickup"

    @field_validator("direction")
    @classmethod
    def _direction_must_be_known(cls, value: str) -> str:
        allowed = {member.value for member in TripDirection}
        if value not in allowed:
            raise ValueError("direction must be 'pickup' or 'drop'")
        return value


class TripStudentUpdate(BaseModel):
    """Outcome snapshot for one student on an in-progress trip.

    Timestamps are recorded server-side; the client only reports which status
    applied and, when explicitly selected, the stop it happened at.
    """

    boarding_status: str | None = None
    boarding_stop_id: int | None = None
    drop_status: str | None = None
    drop_stop_id: int | None = None

    @field_validator("boarding_status")
    @classmethod
    def _boarding_must_be_known(cls, value: str | None) -> str | None:
        if value is not None and value not in {member.value for member in BoardingStatus}:
            raise ValueError("boarding_status must be pending, picked or did_not_board")
        return value

    @field_validator("drop_status")
    @classmethod
    def _drop_must_be_known(cls, value: str | None) -> str | None:
        if value is not None and value not in {member.value for member in DropStatus}:
            raise ValueError("drop_status must be pending, dropped or drop_not_recorded")
        return value

    @model_validator(mode="after")
    def _at_least_one_field(self):
        if all(v is None for v in (self.boarding_status, self.boarding_stop_id,
                                   self.drop_status, self.drop_stop_id)):
            raise ValueError("at least one outcome field is required")
        return self


def _not_blank(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be blank")
    return stripped


class TripCancel(BaseModel):
    cancellation_reason: str

    @field_validator("cancellation_reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        return _not_blank(value)


class TripReopen(BaseModel):
    reopen_reason: str

    @field_validator("reopen_reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        return _not_blank(value)
