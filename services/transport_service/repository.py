from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from common.dependencies import CurrentUser
from common.models import (
    Parent, ParentStudent, Pilot, Route, RouteStop, RouteStudent,
    Staff, Student, Trip, TripStudent, User, Vehicle,
)
from common.security import hash_password
from common.exceptions import (
    AppError, ConflictError, ForbiddenError, NotFoundError,
)


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


def _trip_summary_row(trip: Trip, route: Route) -> dict:
    """Common trip-row shape shared by admin, pilot and parent responses."""
    return {
        "trip_id": trip.trip_id,
        "trip_date": trip.trip_date,
        "route_id": trip.route_id,
        "route_name": route.name,
        "direction": trip.direction,
        "status": trip.status,
        "pilot_id": trip.pilot_id,
        "driver_name": trip.driver_name,
        "vehicle": trip.vehicle,
        "started_at": trip.started_at,
        "ended_at": trip.ended_at,
        "cancelled_at": trip.cancelled_at,
    }


def _trip_query(db: Session, school_id: int):
    """Trip joined with its route, scoped to one school. Driver name is read
    from the immutable snapshot on Trip, so no staff/pilot join is needed."""
    return (
        db.query(Trip, Route)
        .join(Route, Route.route_id == Trip.route_id)
        .filter(Trip.school_id == school_id)
    )


def _apply_date_filters(query, from_date, to_date):
    if from_date is not None:
        query = query.filter(Trip.trip_date >= from_date)
    if to_date is not None:
        query = query.filter(Trip.trip_date <= to_date)
    return query


def _ensure_filter_exists(db: Session, school_id: int, route_id: int | None, pilot_id: int | None):
    """Cross-school filter ids must not silently match other schools' rows."""
    if route_id is not None:
        route = (
            db.query(Route.route_id)
            .filter(Route.route_id == route_id, Route.school_id == school_id)
            .first()
        )
        if route is None:
            raise AppError("Route not found for this school")
    if pilot_id is not None:
        pilot = (
            db.query(Pilot.pilot_id)
            .join(Staff, Staff.staff_id == Pilot.staff_id)
            .filter(Pilot.pilot_id == pilot_id, Staff.school_id == school_id)
            .first()
        )
        if pilot is None:
            raise AppError("Pilot not found for this school")


def _outcome_summary_map(db: Session, trip_ids: list[int]) -> dict[int, str]:
    """One batched query per status axis -> deterministic per-trip summary
    strings. Keeps the admin list free of per-row N+1 badge queries."""
    if not trip_ids:
        return {}
    boarding_counts: dict[int, dict[str, int]] = {}
    for trip_id, status, count in (
        db.query(TripStudent.trip_id, TripStudent.boarding_status, func.count())
        .filter(
            TripStudent.trip_id.in_(trip_ids),
            TripStudent.boarding_status != "pending",
        )
        .group_by(TripStudent.trip_id, TripStudent.boarding_status)
        .all()
    ):
        boarding_counts.setdefault(trip_id, {})[status] = count
    drop_counts: dict[int, dict[str, int]] = {}
    for trip_id, status, count in (
        db.query(TripStudent.trip_id, TripStudent.drop_status, func.count())
        .filter(
            TripStudent.trip_id.in_(trip_ids),
            TripStudent.drop_status != "pending",
        )
        .group_by(TripStudent.trip_id, TripStudent.drop_status)
        .all()
    ):
        drop_counts.setdefault(trip_id, {})[status] = count
    result: dict[int, str] = {}
    for trip_id in trip_ids:
        counts = boarding_counts.get(trip_id, {})
        parts = []
        for key in ("picked", "did_not_board"):
            if counts.get(key):
                parts.append(f"{counts[key]} {key.replace('_', ' ')}")
        counts = drop_counts.get(trip_id, {})
        for key in ("dropped", "drop_not_recorded"):
            if counts.get(key):
                parts.append(f"{counts[key]} {key.replace('_', ' ')}")
        result[trip_id] = " · ".join(parts)
    return result


def list_trips(
    db: Session,
    school_id: int,
    route_id: int | None = None,
    pilot_id: int | None = None,
    direction: str | None = None,
    status: str | None = None,
    from_date=None,
    to_date=None,
) -> list[dict]:
    """Admin history list. Filters that reference another school's route/pilot
    are rejected rather than silently returning an empty/spoofed set."""
    _ensure_filter_exists(db, school_id, route_id, pilot_id)
    query = _trip_query(db, school_id)
    if route_id is not None:
        query = query.filter(Trip.route_id == route_id)
    if pilot_id is not None:
        query = query.filter(Trip.pilot_id == pilot_id)
    if direction is not None:
        query = query.filter(Trip.direction == direction)
    if status is not None:
        query = query.filter(Trip.status == status)
    query = _apply_date_filters(query, from_date, to_date)
    rows = query.order_by(Trip.trip_date.desc(), Trip.trip_id.desc()).all()
    summaries = []
    for trip, route in rows:
        summary = _trip_summary_row(trip, route)
        summaries.append(summary)
    summaries_by_id = {summary["trip_id"]: summary for summary in summaries}
    outcome_summary = _outcome_summary_map(db, [s["trip_id"] for s in summaries])
    for trip_id, text in outcome_summary.items():
        summaries_by_id[trip_id]["outcome_summary"] = text
    for summary in summaries:
        summary.setdefault("outcome_summary", "")
    return summaries


def _trip_students(db: Session, trip_id: int) -> list[dict]:
    """All of a trip's students in one joined query (no per-student N+1)."""
    rows = (
        db.query(TripStudent, Student)
        .join(Student, Student.student_id == TripStudent.student_id)
        .filter(TripStudent.trip_id == trip_id)
        .order_by(Student.name)
        .all()
    )
    return [
        {
            "student_id": trip_student.student_id,
            "student_name": student.name,
            "boarding_status": trip_student.boarding_status,
            "boarding_at": trip_student.boarding_at,
            "boarding_stop_id": trip_student.boarding_stop_id,
            "drop_status": trip_student.drop_status,
            "drop_at": trip_student.drop_at,
            "drop_stop_id": trip_student.drop_stop_id,
        }
        for trip_student, student in rows
    ]


def get_trip_detail(db: Session, school_id: int, trip_id: int) -> dict | None:
    """Admin detail. Returns None when the trip does not exist in this school
    (the router turns that into a 404 without leaking another school's data)."""
    row = _trip_query(db, school_id).filter(Trip.trip_id == trip_id).first()
    if row is None:
        return None
    trip, route = row
    outcome_summary = _outcome_summary_map(db, [trip_id])[trip_id]
    detail = _trip_summary_row(trip, route)
    detail["outcome_summary"] = outcome_summary
    detail.update(
        {
            "cancelled_by": trip.cancelled_by,
            "cancellation_reason": trip.cancellation_reason,
            "reopened_at": trip.reopened_at,
            "reopened_by": trip.reopened_by,
            "reopen_reason": trip.reopen_reason,
            "students": _trip_students(db, trip_id),
        }
    )
    return detail


def list_pilot_trips(
    db: Session,
    school_id: int,
    staff_id: int | None,
    from_date=None,
    to_date=None,
) -> list[dict]:
    """Pilot history. Identity comes only from the caller's linked staff row;
    the pilot_id broadcast never filters anything, so a pilot can never list
    another pilot's trips by passing the id of a route they do not drive."""
    if staff_id is None:
        return []
    pilot = (
        db.query(Pilot)
        .join(Staff, Staff.staff_id == Pilot.staff_id)
        .filter(Pilot.staff_id == staff_id, Staff.school_id == school_id, Pilot.is_active.is_(True))
        .first()
    )
    if pilot is None:
        return []
    query = _trip_query(db, school_id).filter(Trip.pilot_id == pilot.pilot_id)
    query = _apply_date_filters(query, from_date, to_date)
    rows = query.order_by(Trip.trip_date.desc(), Trip.trip_id.desc()).all()
    return [_trip_summary_row(trip, route) for trip, route in rows]


def _trip_students_with_stop_names(db: Session, trip_id: int) -> list[dict]:
    """The trip's historical roster (already joined to Student in one query)
    plus batch-resolved stop names. No per-student or per-stop N+1."""
    students = _trip_students(db, trip_id)
    stop_names = _stop_names(
        db,
        [row.get("boarding_stop_id") for row in students]
        + [row.get("drop_stop_id") for row in students],
    )
    for row in students:
        row["boarding_stop_name"] = stop_names.get(row.get("boarding_stop_id"))
        row["drop_stop_name"] = stop_names.get(row.get("drop_stop_id"))
    return students


def get_pilot_trip_detail(
    db: Session,
    school_id: int,
    trip_id: int,
    current_user: CurrentUser,
) -> dict | None:
    """Detail of ONE of the pilot's OWN trips, roster and all.

    The same isolation as every other transport read: a trip outside the
    caller's school is 404 (does not exist here), while a same-school trip the
    pilot does not currently drive is refused by the shared authorization
    helper. Identity is derived only from the caller's linked staff record.
    """
    trip = _get_trip(db, school_id, trip_id)
    if trip is None:
        return None
    _authorize_trip(db, trip, current_user)
    route = db.query(Route).filter(Route.route_id == trip.route_id).first()
    detail = _trip_summary_row(trip, route)
    detail.update(
        {
            "cancellation_reason": trip.cancellation_reason,
            "reopened_at": trip.reopened_at,
            "reopen_reason": trip.reopen_reason,
            "students": _trip_students_with_stop_names(db, trip.trip_id),
        }
    )
    return detail


def _stop_names(db: Session, stop_ids) -> dict[int, str]:
    stop_ids = {sid for sid in stop_ids if sid is not None}
    if not stop_ids:
        return {}
    rows = db.query(RouteStop).filter(RouteStop.stop_id.in_(stop_ids)).all()
    return {stop.stop_id: stop.name for stop in rows}


def list_child_completed_trips(
    db: Session,
    school_id: int,
    parent_id: int | None,
    student_id: int,
    from_date=None,
    to_date=None,
) -> list[dict]:
    """Parent history. Two hard invariants: only completed trips, and only the
    requested child's own TripStudent snapshot - parents never see siblings'
    boarding/drop rows. A student the caller does not own (including one in
    another school) is refused up front."""
    if parent_id is None:
        raise AppError("User is not linked to a parent account")
    owned = (
        db.query(ParentStudent.parent_id)
        .filter(
            ParentStudent.parent_id == parent_id,
            ParentStudent.student_id == student_id,
        )
        .first()
    )
    if owned is None:
        raise ForbiddenError("You do not have access to this student's trips")
    student = (
        db.query(Student.student_id)
        .filter(Student.student_id == student_id, Student.school_id == school_id)
        .first()
    )
    if student is None:
        raise ForbiddenError("You do not have access to this student's trips")
    query = (
        db.query(Trip, Route, TripStudent)
        .join(Route, Route.route_id == Trip.route_id)
        .join(TripStudent, TripStudent.trip_id == Trip.trip_id)
        .filter(
            Trip.school_id == school_id,
            TripStudent.student_id == student_id,
            Trip.status == "completed",
        )
    )
    query = _apply_date_filters(query, from_date, to_date)
    rows = query.order_by(Trip.trip_date.desc(), Trip.trip_id.desc()).all()
    stop_ids = set()
    for trip, route, trip_student in rows:
        stop_ids.add(trip_student.boarding_stop_id)
        stop_ids.add(trip_student.drop_stop_id)
    names = _stop_names(db, stop_ids)
    result = []
    for trip, route, trip_student in rows:
        row = _trip_summary_row(trip, route)
        row.update(
            {
                "boarding_status": trip_student.boarding_status,
                "boarding_at": trip_student.boarding_at,
                "boarding_stop_id": trip_student.boarding_stop_id,
                "boarding_stop_name": names.get(trip_student.boarding_stop_id),
                "drop_status": trip_student.drop_status,
                "drop_at": trip_student.drop_at,
                "drop_stop_id": trip_student.drop_stop_id,
                "drop_stop_name": names.get(trip_student.drop_stop_id),
            }
        )
        result.append(row)
    return result


# ============================================================================
# TRIP LIFECYCLE (WRITE) — Phase 3
# ============================================================================
#
# The trip lifecycle mirrors a real-day run (see the Trip model docstring):
#
#     scheduled ──► in_progress ──► completed
#        │              │
#        │              ▼
#        └──► cancelled ◄── (reopened/admin, returns to in_progress)
#
# Implemented centrally in `apply_transition` so routers never duplicate the
# state checks. All timestamps are server time; the client cannot supply them.
# ============================================================================

# The one authoritative transition matrix.
_LIFECYCLE_TRANSITIONS = {
    "scheduled": {"in_progress", "cancelled"},   # start / cancel a planned run
    "in_progress": {"completed", "cancelled"},   # finish / abort a live run
    "cancelled": {"in_progress"},                # reopen (admin only)
    "completed": set(),
}

# Migration matrix for the two legs of a TripStudent row. A child's boarding is
# fixed once recorded: pending -> picked / did_not_board, and nothing further.
_BOARDING_TRANSITIONS = {"pending": {"picked", "did_not_board"}}
_DROP_TRANSITIONS = {"pending": {"dropped", "drop_not_recorded"}}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_trip(db: Session, school_id: int, trip_id: int) -> Trip | None:
    return (
        db.query(Trip)
        .filter(Trip.school_id == school_id, Trip.trip_id == trip_id)
        .first()
    )


def resolve_pilot(db: Session, school_id: int, staff_id: int | None) -> Pilot | None:
    """JWT linked_person_id -> Staff -> Pilot, scoped to one school and active only."""
    if staff_id is None:
        return None
    return (
        db.query(Pilot)
        .join(Staff, Staff.staff_id == Pilot.staff_id)
        .filter(
            Staff.school_id == school_id,
            Pilot.staff_id == staff_id,
            Pilot.is_active.is_(True),
            Staff.is_active.is_(True),
        )
        .first()
    )


def _authorize_trip(db: Session, trip: Trip, current_user: CurrentUser, allow_pilot: bool = True) -> None:
    """Admin may act on any in-school trip; a pilot only on a trip of their own
    currently-assigned route. Ownership checks BOTH the snapshot pilot_id (who
    was driving when the trip was created) and the live route assignment, so a
    reassigned driver cannot touch an old trip."""
    if current_user.role == "admin":
        return
    if current_user.role == "pilot" and allow_pilot:
        pilot = resolve_pilot(db, trip.school_id, current_user.linked_person_id)
        if pilot is not None and trip.pilot_id == pilot.pilot_id and trip.route_id == pilot.route_id:
            return
        raise ForbiddenError("This trip is not assigned to you")
    raise ForbiddenError(f"Role '{current_user.role}' cannot operate this trip")


def create_trip(db: Session, school_id: int, current_user, data: dict) -> dict:
    """Create the day's trip for a route and snapshot its current roster.

    Snapshots are drawn from the route's own trusted records. The client does
    not supply school_id, pilot identity, driver_name or vehicle. RouteStudent
    membership at this moment becomes the historical TripStudent roster.
    """
    route_id = data["route_id"]
    trip_date = data["trip_date"]
    direction = data.get("direction", "pickup")

    if current_user.role not in ("admin", "pilot"):
        raise ForbiddenError(f"Role '{current_user.role}' cannot create trips")

    route = (
        db.query(Route)
        .filter(
            Route.route_id == route_id,
            Route.school_id == school_id,
            Route.is_active.is_(True),
        )
        .first()
    )
    if route is None:
        raise NotFoundError("Route not found")

    if current_user.role == "pilot":
        pilot = resolve_pilot(db, school_id, current_user.linked_person_id)
        if pilot is None or route.route_id != pilot.route_id:
            raise ForbiddenError("This route is not assigned to you")
        snapshot_pilot_id = pilot.pilot_id
    else:
        snapshot_pilot_id = route.pilot.pilot_id if route.pilot is not None else None

    duplicate = (
        db.query(Trip.trip_id)
        .filter(
            Trip.route_id == route_id,
            Trip.trip_date == trip_date,
            Trip.direction == direction,
        )
        .first()
    )
    if duplicate is not None:
        raise ConflictError("A trip already exists for this route on this day and direction")

    trip = Trip(
        school_id=school_id,
        route_id=route_id,
        trip_date=trip_date,
        direction=direction,
        status="scheduled",
        pilot_id=snapshot_pilot_id,
        driver_name=route.driver_name,
        vehicle=route.vehicle,
    )
    db.add(trip)
    db.flush()

    roster = (
        db.query(RouteStudent.student_id)
        .filter(RouteStudent.route_id == route_id)
        .all()
    )
    for (student_id,) in roster:
        db.add(TripStudent(trip_id=trip.trip_id, student_id=student_id))

    db.commit()
    db.refresh(trip)
    return get_trip_detail(db, school_id, trip.trip_id)


def apply_transition(db: Session, trip: Trip, target: str, actor: int, reason: str | None = None) -> Trip:
    """The single place a trip changes status.

    Enforces the lifecycle matrix and stamps the appropriate audit/time/reason
    fields. `actor` is the authenticated user's user_id, never client-supplied.
    """
    previous = trip.status
    allowed = _LIFECYCLE_TRANSITIONS.get(previous, set())
    if target not in allowed:
        raise AppError(f"Cannot move trip from '{previous}' to '{target}'")
    trip.status = target
    if target == "in_progress":
        if previous == "scheduled":
            trip.started_at = utcnow()
        else:  # reopened from cancelled
            trip.reopened_at = utcnow()
            trip.reopened_by = actor
            trip.reopen_reason = reason
    elif target == "completed":
        trip.ended_at = utcnow()
    elif target == "cancelled":
        trip.cancelled_at = utcnow()
        trip.cancelled_by = actor
        trip.cancellation_reason = reason
    db.commit()
    db.refresh(trip)
    return trip


def transition_trip(
    db: Session,
    school_id: int,
    trip_id: int,
    current_user: CurrentUser,
    target: str,
    reason: str | None = None,
    allow_pilot: bool = True,
) -> Trip:
    """Resolve, authorize and apply a status transition in one step."""
    trip = _get_trip(db, school_id, trip_id)
    if trip is None:
        raise NotFoundError("Trip not found")
    _authorize_trip(db, trip, current_user, allow_pilot=allow_pilot)
    return apply_transition(db, trip, target, current_user.user_id, reason=reason)


def _validate_stop_for_trip(db: Session, trip: Trip, stop_id: int, required_type: str) -> int:
    """A stop is only valid if it belongs to this trip's route AND matches the
    leg being recorded: pickup-type stops for boarding, drop-type for dropping.
    Nothing is ever derived automatically from the route's schedule."""
    stop = db.query(RouteStop).filter(RouteStop.stop_id == stop_id).first()
    if stop is None or stop.route_id != trip.route_id or stop.stop_type != required_type:
        raise AppError(f"Stop is not a valid {required_type} stop on this trip's route")
    return stop.stop_id


def _apply_boarding(ts: TripStudent, status: str) -> None:
    allowed = _BOARDING_TRANSITIONS.get(ts.boarding_status, set())
    if status not in allowed:
        raise AppError(f"Cannot set boarding status to '{status}' from '{ts.boarding_status}'")
    ts.boarding_status = status
    if status != "pending":
        ts.boarding_at = utcnow()


def _apply_drop(ts: TripStudent, status: str) -> None:
    allowed = _DROP_TRANSITIONS.get(ts.drop_status, set())
    if status not in allowed:
        raise AppError(f"Cannot set drop status to '{status}' from '{ts.drop_status}'")
    ts.drop_status = status
    if status != "pending":
        ts.drop_at = utcnow()


def update_trip_student(
    db: Session,
    school_id: int,
    trip_id: int,
    student_id: int,
    current_user: CurrentUser,
    data: dict,
) -> dict:
    """Record one student's boarding/drop outcome on an in-progress trip.

    The row is located by (trip_id, student_id) so a TripStudent row that does
    not belong to the requested trip or student cannot be reached (a student
    must be on the trip's snapshot roster first). Timestamps are server time.
    """
    trip = _get_trip(db, school_id, trip_id)
    if trip is None:
        raise NotFoundError("Trip not found")
    _authorize_trip(db, trip, current_user)
    if trip.status != "in_progress":
        raise AppError("Student outcomes can only be recorded while the trip is in progress")

    row = (
        db.query(TripStudent)
        .filter(TripStudent.trip_id == trip_id, TripStudent.student_id == student_id)
        .first()
    )
    if row is None:
        raise NotFoundError("Student is not on this trip")

    if data.get("boarding_status") is not None:
        _apply_boarding(row, data["boarding_status"])
    if data.get("drop_status") is not None:
        _apply_drop(row, data["drop_status"])
    if data.get("boarding_stop_id") is not None:
        row.boarding_stop_id = _validate_stop_for_trip(db, trip, data["boarding_stop_id"], "pickup")
    if data.get("drop_stop_id") is not None:
        row.drop_stop_id = _validate_stop_for_trip(db, trip, data["drop_stop_id"], "drop")

    student = db.query(Student).filter(Student.student_id == student_id).first()
    db.commit()
    db.refresh(row)
    return {
        "student_id": row.student_id,
        "student_name": student.name if student else "",
        "boarding_status": row.boarding_status,
        "boarding_at": row.boarding_at,
        "boarding_stop_id": row.boarding_stop_id,
        "drop_status": row.drop_status,
        "drop_at": row.drop_at,
        "drop_stop_id": row.drop_stop_id,
    }
