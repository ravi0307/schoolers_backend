from sqlalchemy.orm import Session

from common.models import LeaveRequest, ParentStudent, Student


def create(db: Session, school_id: int, data: dict) -> LeaveRequest:
    leave = LeaveRequest(school_id=school_id, **data)
    db.add(leave)
    db.commit()
    db.refresh(leave)
    return leave


def list_for_school(db: Session, school_id: int, status: str | None = None) -> list[LeaveRequest]:
    q = db.query(LeaveRequest).filter(LeaveRequest.school_id == school_id, LeaveRequest.is_active.is_(True))
    if status:
        q = q.filter(LeaveRequest.status == status)
    return q.order_by(LeaveRequest.created_at.desc()).all()


def list_for_child_names(db: Session, school_id: int, names: list[str]) -> list[LeaveRequest]:
    if not names:
        return []
    q = db.query(LeaveRequest).filter(
        LeaveRequest.school_id == school_id,
        LeaveRequest.requester_name.in_(names),
        LeaveRequest.is_active.is_(True),
    )
    return q.order_by(LeaveRequest.created_at.desc()).all()


def child_names_of_parent(db: Session, school_id: int, parent_id: int) -> list[str]:
    if parent_id is None:
        return []
    rows = (
        db.query(Student.name)
        .join(ParentStudent, ParentStudent.student_id == Student.student_id)
        .filter(
            ParentStudent.parent_id == parent_id,
            Student.school_id == school_id,
            Student.is_active.is_(True),
        )
        .all()
    )
    return [row[0] for row in rows if row[0]]


def get(db: Session, leave_id: int) -> LeaveRequest | None:
    return db.query(LeaveRequest).filter(LeaveRequest.leave_id == leave_id, LeaveRequest.is_active.is_(True)).first()


def set_status(db: Session, leave: LeaveRequest, status: str) -> LeaveRequest:
    leave.status = status
    db.commit()
    db.refresh(leave)
    return leave
