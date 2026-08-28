from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from common.database import get_db
from common.dependencies import require_role, require_school_scope, CurrentUser
from common.exceptions import NotFoundError
import repository as repo
from schemas import (
    RouteCreate, RouteUpdate, RouteRead, StopCreate, StopRead,
    RouteStudentRead, RouteStudentStatusUpdate,
)

router = APIRouter(prefix="/routes", tags=["transport"])


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


@router.delete("/stops/{stop_id}", status_code=204)
def remove_stop(
    stop_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    repo.remove_stop(db, stop_id)


@router.post("/{route_id}/students/{student_id}", status_code=201)
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
