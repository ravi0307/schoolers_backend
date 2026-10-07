from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
from common.enums import TripDirection, TripStatus
from common.exceptions import AppError, NotFoundError
import repository as repo
from schemas import (
    VehicleCreate, VehicleUpdate, VehicleRead,
    PilotCreate, PilotUpdate, PilotRead,
    RouteCreate, RouteUpdate, RouteRead, StopCreate, StopUpdate, StopRead,
    RouteStudentRead, RouteStudentStatusUpdate, ParentPickDropRead,
    AdminTripDetailRead, AdminTripRead, ParentTripRead, TripSummaryRead,
    PilotTripDetailRead,
    TripCancel, TripCreate, TripReopen, TripStudentRead, TripStudentUpdate,
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
    pilot_record, staff_record, user = pilot
    return repo.update_pilot(
        db, pilot_record, staff_record, user, payload.model_dump(exclude_unset=True)
    )


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
    current_user: CurrentUser = Depends(require_role("parent", "admin", "pilot", "staff")),
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


trips_router = APIRouter(prefix="/trips", tags=["transport"])

_DIRECTIONS = {member.value for member in TripDirection}
_STATUSES = {member.value for member in TripStatus}


def _validate_optional_filter(value, allowed, label):
    if value is not None and value not in allowed:
        raise AppError(f"Invalid {label}: '{value}'")


def _validate_date_range(from_date, to_date):
    if from_date is not None and to_date is not None and from_date > to_date:
        raise AppError("from_date must not be later than to_date")


@trips_router.get("", response_model=list[AdminTripRead])
def list_trips(
    from_date: date | None = None,
    to_date: date | None = None,
    route_id: int | None = None,
    pilot_id: int | None = None,
    direction: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    """Admin trip history. School scope comes from the token, never the client."""
    _validate_optional_filter(direction, _DIRECTIONS, "direction")
    _validate_optional_filter(status, _STATUSES, "status")
    _validate_date_range(from_date, to_date)
    return repo.list_trips(
        db, school_id, route_id, pilot_id, direction, status, from_date, to_date
    )


@trips_router.get("/mine", response_model=list[TripSummaryRead])
def my_trips(
    from_date: date | None = None,
    to_date: date | None = None,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("pilot")),
):
    """Pilot history. The pilot identity is derived from the token's linked
    staff record only; the endpoint accepts no pilot_id that could spoof
    another pilot's trips."""
    _validate_date_range(from_date, to_date)
    staff_id = current_user.linked_person_id
    if staff_id is None:
        return []
    return repo.list_pilot_trips(db, school_id, staff_id, from_date, to_date)


@trips_router.get("/mine/{trip_id}", response_model=PilotTripDetailRead)
def my_trip_detail(
    trip_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("pilot")),
):
    """Detail of ONE of the pilot's own trips, including the historical
    TripStudent roster/outcomes. Identity comes only from the JWT (linked staff
    row -> pilot); no pilot_id/route_id/school_id is accepted from the client.
    Another pilot's same-school trip is 403; a cross-school trip is 404, the
    same isolation used by every other transport read."""
    detail = repo.get_pilot_trip_detail(db, school_id, trip_id, current_user)
    if detail is None:
        raise NotFoundError("Trip not found")
    return detail


@trips_router.get("/children/{student_id}", response_model=list[ParentTripRead])
def parent_child_trips(
    student_id: int,
    from_date: date | None = None,
    to_date: date | None = None,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("parent")),
):
    """Parent view of one of their own children's completed trips. Only the
    requested child's rows are returned; siblings and non-completed trips are
    filtered in SQL, and a student the parent does not own is refused."""
    _validate_date_range(from_date, to_date)
    return repo.list_child_completed_trips(
        db, school_id, current_user.linked_person_id, student_id, from_date, to_date
    )


@trips_router.get("/{trip_id}", response_model=AdminTripDetailRead)
def get_trip_detail(
    trip_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    """Admin detail for one in-school trip; any other school's trip is 404."""
    trip = repo.get_trip_detail(db, school_id, trip_id)
    if trip is None:
        raise NotFoundError("Trip not found")
    return trip


# --- Trip lifecycle (write) ---------------------------------------------
# All transitions run through the single central matrix in the repository;
# these handlers only resolve identity and pass the payload through.

@trips_router.post("", response_model=AdminTripDetailRead, status_code=201)
def create_trip(
    payload: TripCreate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin", "pilot")),
):
    """Create the day's trip for a route. Snapshots (pilot, driver_name,
    vehicle) are taken from the route's own records; route students become the
    historical trip roster. A duplicate route/day/direction is a 409."""
    return repo.create_trip(db, school_id, current_user, payload.model_dump())


@trips_router.post("/{trip_id}/start", response_model=AdminTripDetailRead)
def start_trip(
    trip_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin", "pilot")),
):
    """scheduled -> in_progress; records started_at (server time)."""
    trip = repo.transition_trip(db, school_id, trip_id, current_user, "in_progress")
    return repo.get_trip_detail(db, school_id, trip.trip_id)


@trips_router.post("/{trip_id}/complete", response_model=AdminTripDetailRead)
def complete_trip(
    trip_id: int,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin", "pilot")),
):
    """in_progress -> completed; records ended_at (server time). Pending
    TripStudent outcomes are left pending - finalization stays explicit."""
    trip = repo.transition_trip(db, school_id, trip_id, current_user, "completed")
    return repo.get_trip_detail(db, school_id, trip.trip_id)


@trips_router.post("/{trip_id}/cancel", response_model=AdminTripDetailRead)
def cancel_trip(
    trip_id: int,
    payload: TripCancel,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin", "pilot")),
):
    """Cancel a scheduled or in-progress trip; records cancelled_at/by/reason.
    The actor is the authenticated user, never client-supplied."""
    trip = repo.transition_trip(
        db, school_id, trip_id, current_user, "cancelled",
        reason=payload.cancellation_reason,
    )
    return repo.get_trip_detail(db, school_id, trip.trip_id)


@trips_router.post("/{trip_id}/reopen", response_model=AdminTripDetailRead)
def reopen_trip(
    trip_id: int,
    payload: TripReopen,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    """cancelled -> in_progress on the SAME row (the uniqueness constraint on
    route/date/direction survives). Admin-only: reopening a cancelled trip is
    a correction of an administrative decision."""
    trip = repo.transition_trip(
        db, school_id, trip_id, current_user, "in_progress",
        reason=payload.reopen_reason, allow_pilot=False,
    )
    return repo.get_trip_detail(db, school_id, trip.trip_id)


@trips_router.patch("/{trip_id}/students/{student_id}", response_model=TripStudentRead)
def update_trip_student(
    trip_id: int,
    student_id: int,
    payload: TripStudentUpdate,
    db: Session = Depends(get_db),
    school_id: int = Depends(require_school_scope),
    current_user: CurrentUser = Depends(require_role("admin", "pilot")),
):
    """Record one student's boarding/drop outcome on an in-progress trip.
    Stops are validated against the trip's route; timestamps are server time."""
    return repo.update_trip_student(
        db, school_id, trip_id, student_id, current_user, payload.model_dump()
    )
