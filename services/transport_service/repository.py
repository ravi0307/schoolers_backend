from sqlalchemy.orm import Session

from common.models import (
    Parent, ParentStudent, Pilot, Route, RouteStop, RouteStudent,
    Staff, Student, User, Vehicle,
)
from common.security import hash_password
from common.exceptions import AppError, ConflictError, NotFoundError


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


# The driver API still speaks the old pilot vocabulary; map it onto the
# unified staff columns in one place.
PILOT_TO_STAFF_FIELDS = {
    "full_name": "name",
    "email": "email",
    "phone": "phone",
    "present_address": "present_address",
    "permanent_address": "permanent_address",
    "aadhaar_number": "aadhaar_card",
    "dl_number": "driving_license",
}


def _pilot_response(pilot: Pilot, staff: Staff, user: User | None) -> dict:
    """A pilot row joined to its staff record (personal details) and login.

    The login is optional: a driver does not need an account to be listed.
    """
    return {
        "pilot_id": pilot.pilot_id,
        "staff_id": pilot.staff_id,
        "school_id": staff.school_id,
        "user_id": user.user_id if user else None,
        "role": user.role if user else "pilot",
        "username": user.username if user else None,
        "full_name": staff.name,
        "email": staff.email,
        "phone": staff.phone or "",
        "present_address": staff.present_address,
        "permanent_address": staff.permanent_address,
        "aadhaar_number": staff.aadhaar_card,
        "dl_number": staff.driving_license,
        "license_expiry": pilot.license_expiry,
        "route_id": pilot.route_id,
        "is_active": bool(pilot.is_active and staff.is_active),
    }


def _pilot_query(db: Session, school_id: int):
    return (
        db.query(Pilot, Staff, User)
        .join(Staff, Staff.staff_id == Pilot.staff_id)
        .outerjoin(User, (User.role == "pilot") & (User.linked_person_id == Pilot.staff_id))
        .filter(Staff.school_id == school_id)
    )


def list_pilots(db: Session, school_id: int) -> list[dict]:
    return [
        _pilot_response(pilot, staff, user)
        for pilot, staff, user in _pilot_query(db, school_id)
        .order_by(Staff.name)
        .all()
    ]


def get_pilot(db: Session, school_id: int, pilot_id: int) -> tuple[Pilot, Staff, User | None] | None:
    return (
        _pilot_query(db, school_id)
        .filter(Pilot.pilot_id == pilot_id)
        .first()
    )


def pilot_for_route(db: Session, route_id: int) -> tuple[Pilot, Staff] | None:
    return (
        db.query(Pilot, Staff)
        .join(Staff, Staff.staff_id == Pilot.staff_id)
        .filter(Pilot.route_id == route_id)
        .first()
    )


def create_pilot(db: Session, school_id: int, data: dict) -> dict:
    """Create the driver login + staff record + thin pilots row."""
    username = data.pop("username", None)
    password = data.pop("password", None)
    license_expiry = data.pop("license_expiry", None)
    route_id = data.pop("route_id", None)

    if route_id is not None:
        _assert_route_assignable(db, school_id, route_id, None)

    # Personal details belong on the unified staff row, not the driver row.
    personal = {k: data.pop(k) for k in list(data) if k in PILOT_TO_STAFF_FIELDS}
    if not personal.get("full_name"):
        raise AppError("full_name is required to create a pilot")
    personal.setdefault("phone", "")

    staff = Staff(
        school_id=school_id,
        role="Pilot",
        role_title="Pilot",
        person_type="pilot",
        **{attr: value for key, attr in PILOT_TO_STAFF_FIELDS.items()
           if (value := personal.get(key)) is not None},
    )
    db.add(staff)
    db.flush()

    user = None
    if username and password:
        user = User(
            school_id=school_id,
            role="pilot",
            username=username,
            password_hash=hash_password(password),
        )
        db.add(user)
        db.flush()
        user.linked_person_id = staff.staff_id

    pilot = Pilot(
        staff_id=staff.staff_id,
        license_expiry=license_expiry,
        route_id=route_id,
        is_active=True,
    )
    db.add(pilot)
    db.commit()
    db.refresh(pilot)
    db.refresh(staff)
    if user is not None:
        db.refresh(user)
    return _pilot_response(pilot, staff, user)


def _assert_route_assignable(
    db: Session, school_id: int, route_id: int, exclude_pilot_id: int | None
) -> None:
    """A route has exactly one driver, so refuse to steal an assigned route."""
    taken = (
        db.query(Pilot)
        .join(Staff, Staff.staff_id == Pilot.staff_id)
        .filter(Pilot.route_id == route_id, Staff.school_id == school_id)
        .first()
    )
    if taken and taken.pilot_id != exclude_pilot_id:
        raise ConflictError("That route already has a driver")


def update_pilot(db: Session, pilot: Pilot, staff: Staff, user: User | None, data: dict) -> dict:
    password = data.pop("password", None)
    if user is not None:
        if password is not None:
            user.password_hash = hash_password(password)
        if "username" in data and data["username"]:
            user.username = data.pop("username")
    else:
        data.pop("username", None)
        password = None

    if "route_id" in data:
        route_id = data["route_id"]
        if route_id is not None:
            _assert_route_assignable(db, staff.school_id, route_id, pilot.pilot_id)
        pilot.route_id = route_id

    if "license_expiry" in data:
        pilot.license_expiry = data["license_expiry"]
    if "is_active" in data and data["is_active"] is not None:
        pilot.is_active = data["is_active"]
        staff.is_active = data["is_active"]
        if user is not None:
            user.is_active = data["is_active"]

    # Personal details live on the staff row.
    for key, attr in PILOT_TO_STAFF_FIELDS.items():
        if data.get(key) is not None:
            setattr(staff, attr, data[key])
    db.commit()
    db.refresh(pilot)
    db.refresh(staff)
    if user is not None:
        db.refresh(user)
    return _pilot_response(pilot, staff, user)


def list_routes(db: Session, school_id: int) -> list[Route]:
    return db.query(Route).filter(Route.school_id == school_id, Route.is_active.is_(True)).all()


def get_route(db: Session, school_id: int, route_id: int) -> Route | None:
    return db.query(Route).filter(Route.school_id == school_id, Route.route_id == route_id, Route.is_active.is_(True)).first()


def _set_route_driver(db: Session, school_id: int, route: Route, pilot_id: int) -> None:
    """Point a route at a driver, clearing any previous assignment."""
    pilot = (
        db.query(Pilot)
        .join(Staff, Staff.staff_id == Pilot.staff_id)
        .filter(
            Pilot.pilot_id == pilot_id,
            Staff.school_id == school_id,
            Staff.is_active.is_(True),
        )
        .first()
    )
    if not pilot:
        raise NotFoundError("Driver not found for this school")
    # A route has exactly one driver, so release the previous holder first.
    db.query(Pilot).filter(
        Pilot.route_id == route.route_id, Pilot.pilot_id != pilot.pilot_id
    ).update({Pilot.route_id: None}, synchronize_session=False)
    pilot.route_id = route.route_id


def create_route(db: Session, school_id: int, data: dict) -> Route:
    driver_pilot_id = data.pop("driver_pilot_id", None)
    route = Route(school_id=school_id, **data)
    db.add(route)
    db.flush()
    if driver_pilot_id is not None:
        _set_route_driver(db, school_id, route, driver_pilot_id)
    db.commit()
    db.refresh(route)
    return route


def update_route(db: Session, route: Route, data: dict) -> Route:
    if "driver_pilot_id" in data:
        driver_pilot_id = data.pop("driver_pilot_id")
        if driver_pilot_id is None:
            db.query(Pilot).filter(Pilot.route_id == route.route_id).update(
                {Pilot.route_id: None}, synchronize_session=False
            )
        else:
            _set_route_driver(db, route.school_id, route, driver_pilot_id)
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


def _group_stops_by_name(stops: list[RouteStop]) -> list[dict]:
    """Collapse a route's pickup/drop stop rows into one entry per stop name."""
    groups: dict[str, list[RouteStop]] = {}
    for stop in stops:
        groups.setdefault(stop.name, []).append(stop)
    return [_stop_group(group) for group in groups.values()]


def stops_for_routes(db: Session, route_ids: set[int]) -> dict[int, list[dict]]:
    """Stop schedule for several routes in a single query.

    The parent pick/drop snapshot needs the stops for every route its children
    ride, so this batches them rather than issuing a query per child.
    """
    if not route_ids:
        return {}
    rows = (
        db.query(RouteStop)
        .filter(RouteStop.route_id.in_(route_ids))
        .order_by(RouteStop.route_id, RouteStop.stop_order, RouteStop.stop_id)
        .all()
    )
    by_route: dict[int, list[RouteStop]] = {}
    for row in rows:
        by_route.setdefault(row.route_id, []).append(row)
    return {rid: _group_stops_by_name(stops) for rid, stops in by_route.items()}


def list_stops(db: Session, route_id: int) -> list[dict]:
    stops = (
        db.query(RouteStop)
        .filter(RouteStop.route_id == route_id)
        .order_by(RouteStop.stop_order, RouteStop.stop_id)
        .all()
    )
    return _group_stops_by_name(stops)


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


def list_children_pickdrop(db: Session, school_id: int, parent_id: int) -> list[dict]:
    """Pick/drop snapshot for every active child of a parent at a school.

    Students assigned to an active route get the route details plus their
    current pick/drop status; unassigned children are included with
    status "not_assigned" so the parent portal can show one row per child.
    """
    parent = db.query(Parent).filter(
        Parent.parent_id == parent_id, Parent.school_id == school_id
    ).first()
    if not parent:
        return []
    student_ids = [
        row[0]
        for row in db.query(ParentStudent.student_id)
        .filter(ParentStudent.parent_id == parent_id)
        .all()
    ]
    if not student_ids:
        return []
    assigned = {
        rs.student_id: {
            "route_id": route.route_id,
            "route_name": route.name,
            "vehicle": route.vehicle,
            "driver_name": route.driver_name,
            "status": rs.status,
        }
        for rs, route, student in (
            db.query(RouteStudent, Route, Student)
            .join(Route, Route.route_id == RouteStudent.route_id)
            .join(Student, Student.student_id == RouteStudent.student_id)
            .filter(
                RouteStudent.student_id.in_(student_ids),
                Route.school_id == school_id,
                Route.is_active.is_(True),
                Student.school_id == school_id,
                Student.is_active.is_(True),
            )
            .all()
        )
    }
    students = (
        db.query(Student)
        .filter(
            Student.student_id.in_(student_ids),
            Student.school_id == school_id,
            Student.is_active.is_(True),
        )
        .order_by(Student.name)
        .all()
    )
    stops_by_route = stops_for_routes(
        db, {info["route_id"] for info in assigned.values() if info["route_id"]}
    )
    result = []
    for student in students:
        route = assigned.get(student.student_id)
        result.append(
            {
                "student_id": student.student_id,
                "student_name": student.name,
                "admission_no": student.admission_no,
                "route_id": route["route_id"] if route else None,
                "route_name": route["route_name"] if route else None,
                "vehicle": route["vehicle"] if route else None,
                "driver_name": route["driver_name"] if route else None,
                "status": route["status"] if route else "not_assigned",
                # Empty for an unassigned child, so the client can tell "no
                # route" apart from "route with no stops configured".
                "stops": stops_by_route.get(route["route_id"], []) if route else [],
            }
        )
    return result
