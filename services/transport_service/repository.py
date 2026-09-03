from sqlalchemy.orm import Session

from common.models import Pilot, Route, RouteStop, RouteStudent, User, Vehicle
from common.security import hash_password


def list_vehicles(db: Session, school_id: int) -> list[Vehicle]:
    return (
        db.query(Vehicle)
        .filter(Vehicle.school_id == school_id, Vehicle.is_active.is_(True))
        .order_by(Vehicle.vehicle_number)
        .all()
    )


def get_vehicle(db: Session, school_id: int, vehicle_id: int) -> Vehicle | None:
    return (
        db.query(Vehicle)
        .filter(
            Vehicle.school_id == school_id,
            Vehicle.vehicle_id == vehicle_id,
            Vehicle.is_active.is_(True),
        )
        .first()
    )


def create_vehicle(db: Session, school_id: int, data: dict) -> Vehicle:
    vehicle = Vehicle(school_id=school_id, **data)
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    return vehicle


def update_vehicle(db: Session, vehicle: Vehicle, data: dict) -> Vehicle:
    for key, value in data.items():
        if value is not None:
            setattr(vehicle, key, value)
    db.commit()
    db.refresh(vehicle)
    return vehicle


def _pilot_response(pilot: Pilot, user: User) -> dict:
    return {
        "pilot_id": pilot.pilot_id,
        "user_id": user.user_id,
        "school_id": pilot.school_id,
        "role": user.role,
        "username": user.username,
        "full_name": pilot.full_name,
        "email": pilot.email,
        "phone": pilot.phone,
        "present_address": pilot.present_address,
        "permanent_address": pilot.permanent_address,
        "aadhaar_number": pilot.aadhaar_number,
        "dl_number": pilot.dl_number,
        "is_active": pilot.is_active and user.is_active,
    }


def list_pilots(db: Session, school_id: int) -> list[dict]:
    return [
        _pilot_response(pilot, user)
        for pilot, user in (
            db.query(Pilot, User)
            .join(User, User.user_id == Pilot.user_id)
        .filter(
            Pilot.school_id == school_id,
            User.role == "pilot",
        )
        .order_by(User.username)
        .all()
        )
    ]


def get_pilot(db: Session, school_id: int, pilot_id: int) -> tuple[Pilot, User] | None:
    return (
        db.query(Pilot, User)
        .join(User, User.user_id == Pilot.user_id)
        .filter(
            Pilot.pilot_id == pilot_id,
            Pilot.school_id == school_id,
            User.role == "pilot",
        )
        .first()
    )


def create_pilot(db: Session, school_id: int, data: dict) -> dict:
    username = data.pop("username")
    password = data.pop("password")
    user = User(
        school_id=school_id,
        role="pilot",
        username=username,
        password_hash=hash_password(password),
    )
    db.add(user)
    db.flush()
    pilot = Pilot(user_id=user.user_id, school_id=school_id, **data)
    db.add(pilot)
    db.commit()
    db.refresh(user)
    db.refresh(pilot)
    return _pilot_response(pilot, user)


def update_pilot(db: Session, pilot: Pilot, user: User, data: dict) -> dict:
    password = data.pop("password", None)
    if password is not None:
        user.password_hash = hash_password(password)
    if "username" in data:
        user.username = data.pop("username")
    for key, value in data.items():
        if value is not None:
            setattr(pilot, key, value)
    db.commit()
    db.refresh(user)
    db.refresh(pilot)
    return _pilot_response(pilot, user)


def list_routes(db: Session, school_id: int) -> list[Route]:
    return db.query(Route).filter(Route.school_id == school_id, Route.is_active.is_(True)).all()


def get_route(db: Session, school_id: int, route_id: int) -> Route | None:
    return db.query(Route).filter(Route.school_id == school_id, Route.route_id == route_id, Route.is_active.is_(True)).first()


def create_route(db: Session, school_id: int, data: dict) -> Route:
    route = Route(school_id=school_id, **data)
    db.add(route)
    db.commit()
    db.refresh(route)
    return route


def update_route(db: Session, route: Route, data: dict) -> Route:
    for k, v in data.items():
        if v is not None:
            setattr(route, k, v)
    db.commit()
    db.refresh(route)
    return route


def delete_route(db: Session, route: Route) -> None:
    route.is_active = False
    db.commit()


def add_stop(db: Session, route_id: int, data: dict) -> RouteStop:
    stop = RouteStop(route_id=route_id, **data)
    db.add(stop)
    db.commit()
    db.refresh(stop)
    return stop


def list_stops(db: Session, route_id: int) -> list[RouteStop]:
    return db.query(RouteStop).filter(RouteStop.route_id == route_id).order_by(RouteStop.stop_order).all()


def remove_stop(db: Session, stop_id: int) -> None:
    stop = db.query(RouteStop).filter(RouteStop.stop_id == stop_id).first()
    if stop:
        db.delete(stop)
        db.commit()


def add_student(db: Session, route_id: int, student_id: int) -> RouteStudent:
    existing = db.query(RouteStudent).filter(
        RouteStudent.route_id == route_id, RouteStudent.student_id == student_id
    ).first()
    if existing:
        return existing
    rs = RouteStudent(route_id=route_id, student_id=student_id)
    db.add(rs)
    db.commit()
    db.refresh(rs)
    return rs


def remove_student(db: Session, route_id: int, student_id: int) -> None:
    db.query(RouteStudent).filter(
        RouteStudent.route_id == route_id, RouteStudent.student_id == student_id
    ).delete()
    db.commit()


def list_route_students(db: Session, route_id: int) -> list[RouteStudent]:
    return db.query(RouteStudent).filter(RouteStudent.route_id == route_id).all()


def update_student_status(db: Session, route_id: int, student_id: int, status: str) -> RouteStudent | None:
    rs = db.query(RouteStudent).filter(
        RouteStudent.route_id == route_id, RouteStudent.student_id == student_id
    ).first()
    if rs:
        rs.status = status
        db.commit()
        db.refresh(rs)
    return rs
