from pydantic import BaseModel


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


class PilotRead(BaseModel):
    pilot_id: int
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
    name: str
    stop_time: str
    stop_type: str  # 'pickup' | 'drop'
    stop_order: int = 1


class StopRead(BaseModel):
    stop_id: int
    route_id: int
    name: str
    stop_time: str
    stop_type: str
    stop_order: int

    class Config:
        from_attributes = True


class RouteStudentRead(BaseModel):
    id: int
    route_id: int
    student_id: int
    status: str

    class Config:
        from_attributes = True


class RouteStudentStatusUpdate(BaseModel):
    status: str  # 'pending' | 'picked' | 'dropped'
