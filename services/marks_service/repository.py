from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.models import Mark, Student, Subject, TeacherClassSubject, ParentStudent


def get_for_student(db: Session, student_id: int) -> list[Mark]:
    return db.query(Mark).filter(Mark.student_id == student_id).all()


def is_parent_of(db: Session, parent_id: int, student_id: int) -> bool:
    """True when the parent record owns this student (parent_student link)."""
    exists = db.query(ParentStudent).filter(
        ParentStudent.parent_id == parent_id,
        ParentStudent.student_id == student_id,
    ).first()
    return exists is not None


def student_exists(db: Session, student_id: int) -> bool:
    return db.query(Student.student_id).filter(Student.student_id == student_id).first() is not None


def subject_exists(db: Session, subject_id: int) -> bool:
    return db.query(Subject.subject_id).filter(Subject.subject_id == subject_id).first() is not None


def student_in_school(db: Session, student_id: int, school_id: int) -> bool:
    return db.query(Student.student_id).filter(
        Student.student_id == student_id, Student.school_id == school_id
    ).first() is not None


def teacher_teaches_student(db: Session, teacher_id: int, student_id: int) -> bool:
    """A teacher may read a student's marks once they teach any subject in
    that student's class."""
    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        return False
    exists = db.query(TeacherClassSubject).filter(
        TeacherClassSubject.teacher_id == teacher_id,
        TeacherClassSubject.class_id == student.class_id,
    ).first()
    return exists is not None


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


def get_subject_ids_for_class(db: Session, teacher_id: int, class_id: int) -> list[int]:
    """The set of subject ids a teacher is assigned to teach in one class."""
    rows = db.query(TeacherClassSubject.subject_id).filter(
        TeacherClassSubject.teacher_id == teacher_id,
        TeacherClassSubject.class_id == class_id,
    ).all()
    return [row[0] for row in rows]


def get_for_class(db: Session, class_id: int, subject_ids: list[int] | None = None) -> list[Mark]:
    """All marks for students of one class, optionally limited to subjects."""
    student_ids = db.query(Student.student_id).filter(Student.class_id == class_id)
    q = db.query(Mark).filter(Mark.student_id.in_(student_ids))
    if subject_ids is not None:
        q = q.filter(Mark.subject_id.in_(subject_ids))
    return q.all()


def upsert_mark(
    db: Session,
    student_id: int,
    subject_id: int,
    term: str,
    score: int,
    updated_by: int | None,
    updated_by_user: int | None = None,
) -> Mark:
    stmt = pg_insert(Mark).values(
        student_id=student_id, subject_id=subject_id, term=term,
        score=score, updated_by=updated_by, updated_by_user=updated_by_user,
    ).on_conflict_do_update(
        index_elements=["student_id", "subject_id", "term"],
        set_={"score": score, "updated_by": updated_by, "updated_by_user": updated_by_user},
    )
    db.execute(stmt)
    db.commit()
    return db.query(Mark).filter(
        Mark.student_id == student_id, Mark.subject_id == subject_id, Mark.term == term
    ).first()
