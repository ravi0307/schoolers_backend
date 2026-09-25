from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
from common.exceptions import NotFoundError
import repository as repo
from schemas import (
    VehicleCreate, VehicleUpdate, VehicleRead,
    PilotCreate, PilotUpdate, PilotRead,
    RouteCreate, RouteUpdate, RouteRead, StopCreate, StopUpdate, StopRead,
    RouteStudentRead, RouteStudentStatusUpdate, ParentPickDropRead,
)

router = APIRouter(prefix="/routes", tags=["transport"])


@router.get("/vehicles", response_model=list[VehicleRead])
def list_vehicles(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "admin", "pilot")),
):
    return repo.list_vehicles(db, school_id)


@router.post("/vehicles", response_model=VehicleRead, status_code=201)
def create_vehicle(
    payload: VehicleCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.create_vehicle(db, school_id, payload.model_dump())


@router.put("/vehicles/{vehicle_id}", response_model=VehicleRead)
@router.patch("/vehicles/{vehicle_id}", response_model=VehicleRead)
def update_vehicle(
    vehicle_id: int,
    payload: VehicleUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    vehicle = repo.get_vehicle(db, school_id, vehicle_id)
    if not vehicle:
        raise NotFoundError("Vehicle not found")
    return repo.update_vehicle(db, vehicle, payload.model_dump(exclude_unset=True))


@router.get("/vehicles/{vehicle_id}", response_model=VehicleRead)
def get_vehicle(
    vehicle_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "admin", "pilot")),
):
    vehicle = repo.get_vehicle(db, school_id, vehicle_id)
    if not vehicle:
        raise NotFoundError("Vehicle not found")
    return vehicle


@router.get("/pilots", response_model=list[PilotRead])
def list_pilots(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.list_pilots(db, school_id)


@router.post("/pilots", response_model=PilotRead, status_code=201)
def create_pilot(
    payload: PilotCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.create_pilot(school_id=school_id, db=db, data=payload.model_dump())


@router.put("/pilots/{pilot_id}", response_model=PilotRead)
@router.patch("/pilots/{pilot_id}", response_model=PilotRead)
def update_pilot(
    pilot_id: int,
    payload: PilotUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    pilot = repo.get_pilot(db, school_id, pilot_id)
    if not pilot:
        raise NotFoundError("Pilot not found")
    pilot_record, user = pilot
    return repo.update_pilot(db, pilot_record, user, payload.model_dump(exclude_unset=True))


@router.get("/pilots/{pilot_id}", response_model=PilotRead)
def get_pilot(
    pilot_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    pilot = repo.get_pilot(db, school_id, pilot_id)
    if not pilot:
        raise NotFoundError("Pilot not found")
    return repo._pilot_response(*pilot)


@router.get("", response_model=list[RouteRead])
def list_routes(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent", "admin", "pilot")),
):
    return repo.list_routes(db, school_id)


@router.post("", response_model=RouteRead, status_code=201)
def create_route(
    payload: RouteCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.create_route(db, school_id, payload.model_dump())


@router.patch("/{route_id}", response_model=RouteRead)
def update_route(
    route_id: int,
    payload: RouteUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    route = repo.get_route(db, school_id, route_id)
    if not route:
        raise NotFoundError("Route not found")
    return repo.update_route(db, route, payload.model_dump(exclude_unset=True))


@router.delete("/{route_id}", status_code=204)
def delete_route(
    route_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    route = repo.get_route(db, school_id, route_id)
    if not route:
        raise NotFoundError("Route not found")
    repo.delete_route(db, route)


@router.post("/{route_id}/stops", response_model=StopRead, status_code=201)
def add_stop(
    route_id: int,
    payload: StopCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.add_stop(db, route_id, payload.model_dump())


@router.get("/{route_id}/stops", response_model=list[StopRead])
def list_stops(
    route_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("parent", "admin", "pilot")),
):
    return repo.list_stops(db, route_id)


@router.patch("/stops/{stop_id}", response_model=StopRead)
def update_stop(
    stop_id: int,
    payload: StopUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    stop = repo.get_stop(db, stop_id)
    if not stop:
        raise NotFoundError("Stop not found")
    return repo.update_stop(db, stop, payload.model_dump(exclude_unset=True))


@router.delete("/stops/{stop_id}", status_code=204)
def remove_stop(
    stop_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    repo.remove_stop(db, stop_id)


@router.post("/{route_id}/students/{student_id}", response_model=RouteStudentRead, status_code=201)
def add_student_to_route(
    route_id: int,
    student_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    return repo.add_student(db, route_id, student_id)


@router.delete("/{route_id}/students/{student_id}", status_code=204)
def remove_student_from_route(
    route_id: int,
    student_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    repo.remove_student(db, route_id, student_id)


@router.get("/{route_id}/students", response_model=list[RouteStudentRead])
def list_route_students(
    route_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("parent", "admin", "pilot")),
):
    return repo.list_route_students(db, route_id)


@router.patch("/{route_id}/students/{student_id}/status", response_model=RouteStudentRead)
def update_pickup_drop_status(
    route_id: int,
    student_id: int,
    payload: RouteStudentStatusUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("pilot", "admin")),
):
    rs = repo.update_student_status(db, route_id, student_id, payload.status)
    if not rs:
        raise NotFoundError("Student is not on this route")
    return rs


@router.get("/mine", response_model=list[ParentPickDropRead])
def my_pickdrop_status(
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent")),
):
    """Pick/drop status for the logged-in parent's children."""
    parent_id = current_user.linked_person_id
    if parent_id is None:
        return []
    return repo.list_children_pickdrop(db, school_id, parent_id)
