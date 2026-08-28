from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.models import Mark, Student, TeacherClassSubject


def get_for_student(db: Session, student_id: int) -> list[Mark]:
    return db.query(Mark).filter(Mark.student_id == student_id).all()


def teacher_can_grade(db: Session, teacher_id: int, student_id: int, subject_id: int) -> bool:
    """A teacher may only grade a subject they're actually assigned to teach
    in the student's own class."""
    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        return False
    exists = db.query(TeacherClassSubject).filter(
        TeacherClassSubject.teacher_id == teacher_id,
        TeacherClassSubject.class_id == student.class_id,
        TeacherClassSubject.subject_id == subject_id,
    ).first()
    return exists is not None


def upsert_mark(db: Session, student_id: int, subject_id: int, term: str, score: int, updated_by: int | None) -> Mark:
    stmt = pg_insert(Mark).values(
        student_id=student_id, subject_id=subject_id, term=term, score=score, updated_by=updated_by,
    ).on_conflict_do_update(
        index_elements=["student_id", "subject_id", "term"],
        set_={"score": score, "updated_by": updated_by},
    )
    db.execute(stmt)
    db.commit()
    return db.query(Mark).filter(
        Mark.student_id == student_id, Mark.subject_id == subject_id, Mark.term == term
    ).first()
