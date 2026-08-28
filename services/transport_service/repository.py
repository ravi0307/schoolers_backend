from sqlalchemy.orm import Session

from common.models import Route, RouteStop, RouteStudent


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
