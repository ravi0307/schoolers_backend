from sqlalchemy.orm import Session

from common.models import LeaveRequest


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


def get(db: Session, leave_id: int) -> LeaveRequest | None:
    return db.query(LeaveRequest).filter(LeaveRequest.leave_id == leave_id, LeaveRequest.is_active.is_(True)).first()


def set_status(db: Session, leave: LeaveRequest, status: str) -> LeaveRequest:
    leave.status = status
    db.commit()
    db.refresh(leave)
    return leave
