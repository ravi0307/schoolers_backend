from sqlalchemy.orm import Session

from common.models import SchoolClass, Subject, Period, Holiday


def list_classes(db: Session, school_id: int) -> list[SchoolClass]:
    return db.query(SchoolClass).filter(SchoolClass.school_id == school_id, SchoolClass.is_active.is_(True)).order_by(SchoolClass.name).all()


def get_class(db: Session, school_id: int, class_id: int) -> SchoolClass | None:
    return db.query(SchoolClass).filter(
        SchoolClass.school_id == school_id, SchoolClass.class_id == class_id, SchoolClass.is_active.is_(True)
    ).first()


def create_class(db: Session, school_id: int, data: dict) -> SchoolClass:
    cls = SchoolClass(school_id=school_id, **data)
    db.add(cls)
    db.commit()
    db.refresh(cls)
    return cls


def update_class(db: Session, cls: SchoolClass, data: dict) -> SchoolClass:
    for k, v in data.items():
        if v is not None:
            setattr(cls, k, v)
    db.commit()
    db.refresh(cls)
    return cls


def delete_class(db: Session, cls: SchoolClass) -> None:
    cls.is_active = False
    db.commit()


def list_subjects(db: Session) -> list[Subject]:
    return db.query(Subject).order_by(Subject.name).all()


def list_periods(db: Session) -> list[Period]:
    return db.query(Period).order_by(Period.period_no).all()


def update_period(db: Session, period_id: int, period_time: str) -> Period | None:
    period = db.query(Period).filter(Period.period_id == period_id).first()
    if period:
        period.period_time = period_time
        db.commit()
        db.refresh(period)
    return period


def list_holidays(db: Session, school_id: int) -> list[Holiday]:
    return db.query(Holiday).filter(Holiday.school_id == school_id).all()


def set_holiday(db: Session, school_id: int, day: str, is_holiday: bool) -> Holiday | None:
    holiday = db.query(Holiday).filter(
        Holiday.school_id == school_id, Holiday.day_of_week == day
    ).first()
    if holiday:
        holiday.is_holiday = is_holiday
        db.commit()
        db.refresh(holiday)
    return holiday
