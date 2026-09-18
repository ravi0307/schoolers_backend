from pydantic import BaseModel, field_validator, model_validator

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
    username: str
    password: str
    full_name: str
    phone: str
    email: str | None = None
    present_address: str | None = None
    permanent_address: str | None = None
    aadhaar_number: str | None = None
    dl_number: str | None = None

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
    user_id: int
    school_id: int
    role: str
    username: str
    full_name: str
    email: str | None
    phone: str
    present_address: str | None
    permanent_address: str | None
    aadhaar_number: str | None
    dl_number: str | None
    is_active: bool

    class Config:
        from_attributes = True


class RouteCreate(BaseModel):
    name: str
    vehicle: str
    driver_name: str
    status: str = "Scheduled"


class RouteUpdate(BaseModel):
    name: str | None = None
    vehicle: str | None = None
    driver_name: str | None = None
    status: str | None = None


class RouteRead(BaseModel):
    route_id: int
    school_id: int
    name: str
    vehicle: str
    driver_name: str
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
