from sqlalchemy.orm import Session

from common.models import Pilot, Route, RouteStop, RouteStudent, Student, User, Vehicle
from common.security import hash_password
from common.exceptions import ConflictError


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


def add_stop(db: Session, route_id: int, data: dict) -> dict | None:
    name = data["stop_name"]
    if data.get("pickup_time"):
        db.add(RouteStop(
            route_id=route_id,
            name=name,
            stop_time=data["pickup_time"],
            stop_type="pickup",
            stop_order=data["pickup_order"],
        ))
    if data.get("drop_time"):
        db.add(RouteStop(
            route_id=route_id,
            name=name,
            stop_time=data["drop_time"],
            stop_type="drop",
            stop_order=data["drop_order"],
        ))
    db.commit()
    return get_stop_group(db, route_id, name)


def _stop_group(stops: list[RouteStop]) -> dict:
    pickup = next((stop for stop in stops if stop.stop_type == "pickup"), None)
    drop = next((stop for stop in stops if stop.stop_type == "drop"), None)
    representative = pickup or drop
    return {
        "stop_id": representative.stop_id,
        "route_id": representative.route_id,
        "stop_name": representative.name,
        "pickup_time": pickup.stop_time if pickup else None,
        "pickup_order": pickup.stop_order if pickup else None,
        "drop_time": drop.stop_time if drop else None,
        "drop_order": drop.stop_order if drop else None,
        "pickup_stop_id": pickup.stop_id if pickup else None,
        "drop_stop_id": drop.stop_id if drop else None,
    }


def get_stop_group(db: Session, route_id: int, name: str) -> dict | None:
    stops = (
        db.query(RouteStop)
        .filter(RouteStop.route_id == route_id, RouteStop.name == name)
        .order_by(RouteStop.stop_order, RouteStop.stop_id)
        .all()
    )
    return _stop_group(stops) if stops else None


def list_stops(db: Session, route_id: int) -> list[dict]:
    stops = (
        db.query(RouteStop)
        .filter(RouteStop.route_id == route_id)
        .order_by(RouteStop.stop_order, RouteStop.stop_id)
        .all()
    )
    groups: dict[str, list[RouteStop]] = {}
    for stop in stops:
        groups.setdefault(stop.name, []).append(stop)
    return [_stop_group(group) for group in groups.values()]


def get_stop(db: Session, stop_id: int) -> RouteStop | None:
    return db.query(RouteStop).filter(RouteStop.stop_id == stop_id).first()


def update_stop(db: Session, stop: RouteStop, data: dict) -> dict:
    stops = (
        db.query(RouteStop)
        .filter(RouteStop.route_id == stop.route_id, RouteStop.name == stop.name)
        .all()
    )
    by_type = {item.stop_type: item for item in stops}
    name = data.get("stop_name") or stop.name
    for stop_type in ("pickup", "drop"):
        time_key = f"{stop_type}_time"
        order_key = f"{stop_type}_order"
        item = by_type.get(stop_type)
        if time_key in data and data[time_key] is not None:
            stop_time = data[time_key]
            stop_order = data.get(order_key)
            if item is None:
                item = RouteStop(
                    route_id=stop.route_id,
                    name=name,
                    stop_type=stop_type,
                    stop_time=stop_time,
                    stop_order=stop_order,
                )
                db.add(item)
            else:
                item.stop_time = stop_time
                if stop_order is not None:
                    item.stop_order = stop_order
        elif item is not None and order_key in data and data[order_key] is not None:
            item.stop_order = data[order_key]
        if item is not None:
            item.name = name
    for item in stops:
        item.name = name
    db.commit()
    return get_stop_group(db, stop.route_id, name)


def remove_stop(db: Session, stop_id: int) -> None:
    stop = db.query(RouteStop).filter(RouteStop.stop_id == stop_id).first()
    if stop:
        db.delete(stop)
        db.commit()


def route_student_response(route_student: RouteStudent, student: Student) -> dict:
    return {
        "id": route_student.id,
        "route_id": route_student.route_id,
        "student_id": route_student.student_id,
        "student_name": student.name,
        "admission_no": student.admission_no,
        "status": route_student.status,
    }


def add_student(db: Session, route_id: int, student_id: int) -> dict:
    student = db.query(Student).filter(
        Student.student_id == student_id,
        Student.is_active.is_(True),
    ).first()
    if not student:
        raise ConflictError("Student not found")
    existing = db.query(RouteStudent).filter(
        RouteStudent.route_id == route_id, RouteStudent.student_id == student_id
    ).first()
    if existing:
        return route_student_response(existing, student)
    assigned_route = (
        db.query(RouteStudent)
        .filter(RouteStudent.student_id == student_id)
        .first()
    )
    if assigned_route:
        raise ConflictError(
            "Student is already assigned to another route. Remove the student "
            "from the current route before moving them."
        )
    rs = RouteStudent(route_id=route_id, student_id=student_id)
    db.add(rs)
    db.commit()
    db.refresh(rs)
    return route_student_response(rs, student)


def remove_student(db: Session, route_id: int, student_id: int) -> None:
    db.query(RouteStudent).filter(
        RouteStudent.route_id == route_id, RouteStudent.student_id == student_id
    ).delete()
    db.commit()


def list_route_students(db: Session, route_id: int) -> list[dict]:
    rows = (
        db.query(RouteStudent, Student)
        .join(Student, Student.student_id == RouteStudent.student_id)
        .filter(RouteStudent.route_id == route_id)
        .order_by(Student.name)
        .all()
    )
    return [route_student_response(route_student, student) for route_student, student in rows]


def update_student_status(db: Session, route_id: int, student_id: int, status: str) -> dict | None:
    rs = db.query(RouteStudent).filter(
        RouteStudent.route_id == route_id, RouteStudent.student_id == student_id
    ).first()
    if rs:
        rs.status = status
        db.commit()
        db.refresh(rs)
        student = db.query(Student).filter(Student.student_id == student_id).first()
        return route_student_response(rs, student) if student else None
    return None
