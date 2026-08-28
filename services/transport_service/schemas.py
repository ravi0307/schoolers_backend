from pydantic import BaseModel


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
