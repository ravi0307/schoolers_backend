from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.models import (
    Mark, MarkCorrection, SchoolClass, Student, Subject, TeacherClassSubject,
    ParentStudent, Examination, ExaminationSession, AcademicPeriod, GradeLevel,
    StudentSubject,
)
from common.exceptions import AppError, ConflictError, NotFoundError


def get_for_student(
    db: Session,
    student_id: int,
    published_only: bool = False,
    include_withheld: bool = True,
) -> list[Mark]:
    """Parent/student views pass published_only=True (and usually exclude
    withheld); staff views keep the defaults and see drafts with badges."""
    q = db.query(Mark).filter(Mark.student_id == student_id)
    if published_only:
        q = q.filter(Mark.workflow_status == "published")
    if not include_withheld:
        q = q.filter(Mark.is_withheld.is_(False))
    return q.all()


def is_parent_of(db: Session, parent_id: int, student_id: int) -> bool:
    """True when the parent record owns this student (parent_student link)."""
    exists = db.query(ParentStudent).filter(
        ParentStudent.parent_id == parent_id,
        ParentStudent.student_id == student_id,
    ).first()
    return exists is not None


def student_exists(db: Session, student_id: int) -> bool:
    return db.query(Student.student_id).filter(Student.student_id == student_id).first() is not None


def subject_exists(db: Session, subject_id: int, school_id: int | None = None) -> bool:
    query = db.query(Subject.subject_id).filter(Subject.subject_id == subject_id)
    if school_id is not None:
        query = query.filter(Subject.school_id == school_id)
    return query.first() is not None


def student_in_school(db: Session, student_id: int, school_id: int) -> bool:
    return db.query(Student.student_id).filter(
        Student.student_id == student_id, Student.school_id == school_id
    ).first() is not None


def class_in_school(db: Session, class_id: int, school_id: int) -> bool:
    return db.query(SchoolClass.class_id).filter(
        SchoolClass.class_id == class_id, SchoolClass.school_id == school_id
    ).first() is not None


def teacher_teaches_student(db: Session, teacher_id: int, student_id: int) -> bool:
    """A teacher may read a student's marks once they teach any subject in
    that student's class."""
    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        return False
    exists = db.query(TeacherClassSubject).filter(
        TeacherClassSubject.staff_id == teacher_id,
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
        TeacherClassSubject.staff_id == teacher_id,
        TeacherClassSubject.class_id == student.class_id,
        TeacherClassSubject.subject_id == subject_id,
    ).first()
    return exists is not None


def get_subject_ids_for_class(db: Session, teacher_id: int, class_id: int) -> list[int]:
    """The set of subject ids a teacher is assigned to teach in one class."""
    rows = db.query(TeacherClassSubject.subject_id).filter(
        TeacherClassSubject.staff_id == teacher_id,
        TeacherClassSubject.class_id == class_id,
    ).all()
    return [row[0] for row in rows]


def get_for_class(
    db: Session,
    class_id: int,
    subject_ids: list[int] | None = None,
    term: str | None = None,
    published_only: bool = False,
    include_withheld: bool = True,
) -> list[Mark]:
    """All marks for students of one class, optionally limited to subjects
    and term. The term filter lives in the query (not Python post-filtering)
    so scoped reads never over-fetch before filtering."""
    student_ids = db.query(Student.student_id).filter(Student.class_id == class_id)
    q = db.query(Mark).filter(Mark.student_id.in_(student_ids))
    if subject_ids is not None:
        q = q.filter(Mark.subject_id.in_(subject_ids))
    if term is not None:
        q = q.filter(Mark.term == term)
    if published_only:
        q = q.filter(Mark.workflow_status == "published")
    if not include_withheld:
        q = q.filter(Mark.is_withheld.is_(False))
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
    """Legacy term-scoped write. Behavior preserved exactly: the row is
    immediately visible (published/present, marks format, max 100), matching
    what parents saw before Phase 4. New examination-scoped writes use
    create_session_mark() instead and start as drafts."""
    values = dict(
        student_id=student_id, subject_id=subject_id, term=term,
        score=score, max_marks=100, grade_level_id=None,
        result_format="marks", workflow_status="published",
        assessment_status="present", is_withheld=False,
        examination_session_id=None,
        updated_by=updated_by, updated_by_user=updated_by_user,
    )
    stmt = pg_insert(Mark).values(**values).on_conflict_do_update(
        index_elements=["student_id", "subject_id", "term"],
        index_where=Mark.examination_session_id.is_(None),
        set_={
            k: v for k, v in values.items()
            if k not in ("student_id", "subject_id", "term")
        } | {"updated_at": func.now()},
    )
    db.execute(stmt)
    db.commit()
    return db.query(Mark).filter(
        Mark.student_id == student_id, Mark.subject_id == subject_id, Mark.term == term
    ).first()


# ---- Session marks lifecycle (Phase 4) ----
# Session-scoped marks start as drafts and move draft -> submitted ->
# published. Legacy term-scoped writes (upsert_mark above) keep their
# immediately-visible behavior and never touch this flow.


def teacher_assigned(db: Session, staff_id: int, class_id: int, subject_id: int) -> bool:
    """Class-level version of teacher_can_grade for session operations."""
    return (
        db.query(TeacherClassSubject)
        .filter(
            TeacherClassSubject.staff_id == staff_id,
            TeacherClassSubject.class_id == class_id,
            TeacherClassSubject.subject_id == subject_id,
        )
        .first()
        is not None
    )


def student_enrolled_for_session(
    db: Session, student_id: int, subject_id: int, year_id: int
) -> bool:
    """Opt-in enrollment gate (Phase 5): students with NO rows for the year
    pass (school hasn't adopted enrollment); students with rows need an
    enrolled row for the subject. A non-enrolled student is therefore never
    recorded as zero or missing — entry refuses them outright."""
    any_rows = (
        db.query(StudentSubject)
        .filter(
            StudentSubject.student_id == student_id,
            StudentSubject.academic_year_id == year_id,
        )
        .first()
    )
    if any_rows is None:
        return True
    return (
        db.query(StudentSubject)
        .filter(
            StudentSubject.student_id == student_id,
            StudentSubject.subject_id == subject_id,
            StudentSubject.academic_year_id == year_id,
            StudentSubject.state == "enrolled",
        )
        .first()
        is not None
    )


def get_session_with_exam(db: Session, session_id: int):
    """A session plus its examination, or (None, None)."""
    session = (
        db.query(ExaminationSession)
        .filter(ExaminationSession.session_id == session_id)
        .first()
    )
    if session is None:
        return None, None
    exam = db.query(Examination).filter(Examination.exam_id == session.exam_id).first()
    return session, exam


def resolve_grade_level(db: Session, scale_id: int, label: str):
    return (
        db.query(GradeLevel)
        .filter(GradeLevel.scale_id == scale_id, GradeLevel.label == label)
        .first()
    )


def default_term_for_exam(db: Session, exam) -> str:
    if exam.period_id is not None:
        period = (
            db.query(AcademicPeriod)
            .filter(AcademicPeriod.period_id == exam.period_id)
            .first()
        )
        if period is not None:
            return period.name
    return "Term 1"


def validate_session_payload(db: Session, session, exam, payload: dict) -> dict:
    """Normalize one entry into mark columns. Raises 400/422, never guesses.

    - present + marks format: score required, 0 <= score <= session max.
    - present + grade format: grade label required, must belong to the
      examination's pinned scale; no score accepted.
    - absent/missing/exempt: score and grade must both be absent (422
      otherwise) so a zero can never masquerade as an absence.
    """
    status = payload.get("assessment_status", "present")
    want_marks = exam.result_format_expectation == "marks"
    score = payload.get("score")
    grade_label = payload.get("grade_label")
    if status != "present":
        if score is not None or grade_label is not None:
            raise AppError(
                f"A '{status}' assessment must not carry a score or grade",
                status_code=422,
            )
        return {"assessment_status": status, "score": None, "grade_level_id": None,
                "max_marks": None, "result_format": exam.result_format_expectation}
    if want_marks:
        if grade_label is not None:
            raise AppError("A marks-wise entry must not carry a grade", status_code=400)
        if score is None:
            raise AppError("A present marks-wise entry requires a score", status_code=400)
        try:
            score = int(score)
        except (TypeError, ValueError):
            raise AppError("Score must be a whole number", status_code=400)
        if score < 0 or score > session.max_marks:
            raise AppError(
                f"Score must be between 0 and {session.max_marks}", status_code=400
            )
        return {"assessment_status": "present", "score": score,
                "grade_level_id": None, "max_marks": session.max_marks,
                "result_format": "marks"}
    if score is not None:
        raise AppError("A grade-wise entry must not carry a score", status_code=400)
    if not grade_label:
        raise AppError("A present grade-wise entry requires a grade", status_code=400)
    level = resolve_grade_level(db, exam.grade_scale_id, str(grade_label).strip())
    if level is None:
        raise AppError(f"Grade '{grade_label}' is not in this examination's scale", status_code=400)
    return {"assessment_status": "present", "score": None,
            "grade_level_id": level.level_id, "max_marks": None,
            "result_format": "grade"}


def create_session_mark(
    db: Session, session, exam, student_id: int, subject_id: int,
    payload: dict, user_id: int | None,
):
    if subject_id != session.subject_id:
        raise AppError("Subject does not match the examination session", status_code=400)
    student = db.query(Student).filter(Student.student_id == student_id).first()
    if student is None or student.class_id != session.class_id:
        raise AppError("Student is not in the session's class", status_code=400)
    if not student_enrolled_for_session(db, student_id, subject_id, exam.year_id):
        raise AppError(
            "Student is not enrolled in this subject for the academic year",
            status_code=400,
        )
    if (
        db.query(Mark)
        .filter(Mark.examination_session_id == session.session_id, Mark.student_id == student_id)
        .first()
        is not None
    ):
        raise ConflictError("A mark already exists for this student and session")
    term = payload.get("term") or default_term_for_exam(db, exam)
    mark = Mark(
        student_id=student_id,
        subject_id=subject_id,
        term=term,
        examination_session_id=session.session_id,
        workflow_status="draft",
        is_withheld=False,
        updated_by_user=user_id,
        **validate_session_payload(db, session, exam, payload),
    )
    db.add(mark)
    db.commit()
    db.refresh(mark)
    return mark


def update_session_mark(db: Session, mark, payload: dict):
    """Edit a draft freely. Editing a submitted mark returns it to draft so
    it passes review again. Published rows move only via correct_mark."""
    if mark.workflow_status == "published":
        raise ConflictError("Published marks change only through the correction flow")
    session, exam = get_session_with_exam(db, mark.examination_session_id)
    if session is None or exam is None:
        raise NotFoundError("Examination session not found")
    if "subject_id" in payload and payload["subject_id"] != mark.subject_id:
        raise AppError("Subject does not match the examination session", status_code=400)
    term = payload.get("term", mark.term)
    cols = validate_session_payload(db, session, exam, {**payload, "term": term})
    mark.term = term
    for k, v in cols.items():
        setattr(mark, k, v)
    if mark.workflow_status == "submitted":
        mark.workflow_status = "draft"
    db.commit()
    db.refresh(mark)
    return mark


def submit_session_marks(db: Session, session):
    """Draft -> submitted for one session. Students without rows are
    reported as missing, never invented: the response names them."""
    drafts = (
        db.query(Mark)
        .filter(
            Mark.examination_session_id == session.session_id,
            Mark.workflow_status == "draft",
        )
        .all()
    )
    for mark in drafts:
        mark.workflow_status = "submitted"
    marked_ids = {
        m.student_id
        for m in db.query(Mark.student_id)
        .filter(Mark.examination_session_id == session.session_id)
        .all()
    }
    missing = [
        s.student_id
        for s in db.query(Student.student_id)
        .filter(Student.class_id == session.class_id, Student.is_active.is_(True))
        .all()
        if s.student_id not in marked_ids
    ]
    db.commit()
    return {"submitted": len(drafts), "missing_student_ids": missing}


def publish_session_marks(db: Session, session):
    """Submitted -> published. Drafts stay drafts; callers re-submit first."""
    rows = (
        db.query(Mark)
        .filter(
            Mark.examination_session_id == session.session_id,
            Mark.workflow_status == "submitted",
        )
        .all()
    )
    for mark in rows:
        mark.workflow_status = "published"
    db.commit()
    return {"published": len(rows)}


def withhold_marks(db: Session, mark_ids: list[int], reason: str, user_id: int | None):
    rows = (
        db.query(Mark)
        .filter(Mark.mark_id.in_(mark_ids), Mark.workflow_status == "published")
        .all()
    )
    for mark in rows:
        mark.is_withheld = True
        mark.withheld_reason = reason
        mark.withheld_by = user_id
    db.commit()
    return {"withheld": len(rows)}


def unwithhold_marks(db: Session, mark_ids: list[int]):
    rows = (
        db.query(Mark)
        .filter(Mark.mark_id.in_(mark_ids), Mark.is_withheld.is_(True))
        .all()
    )
    for mark in rows:
        mark.is_withheld = False
        mark.withheld_reason = None
        mark.withheld_by = None
        mark.withheld_at = None
    db.commit()
    return {"unwithheld": len(rows)}


def correct_mark(
    db: Session, mark, payload: dict, reason: str, user_id: int | None
):
    """The only way a published mark changes: append a correction row with
    both sides, then move the mark. The original stays auditable here."""
    if mark.workflow_status != "published":
        raise ConflictError("Only published marks use the correction flow")
    session, exam = get_session_with_exam(db, mark.examination_session_id)
    if session is None or exam is None:
        raise NotFoundError("Examination session not found")
    cols = validate_session_payload(db, session, exam, payload)
    correction = MarkCorrection(
        mark_id=mark.mark_id,
        old_score=mark.score,
        old_grade_level_id=mark.grade_level_id,
        new_score=cols["score"],
        new_grade_level_id=cols["grade_level_id"],
        reason=reason,
        corrected_by=user_id,
        approved_by=user_id,
    )
    db.add(correction)
    for k, v in cols.items():
        setattr(mark, k, v)
    if "term" in payload and payload["term"]:
        mark.term = payload["term"]
    db.commit()
    db.refresh(mark)
    return mark, correction


def session_max_entered_score(db: Session, session_id: int):
    """Highest present numeric score on a session, or None. Guards session
    maximum-mark reductions (academics service)."""
    return (
        db.query(func.max(Mark.score))
        .filter(
            Mark.examination_session_id == session_id,
            Mark.assessment_status == "present",
            Mark.score.is_not(None),
        )
        .scalar()
    )


def get_session_in_school(db: Session, school_id: int, session_id: int):
    """A session plus its examination, both constrained to one school."""
    row = (
        db.query(ExaminationSession, Examination)
        .join(Examination, Examination.exam_id == ExaminationSession.exam_id)
        .filter(
            ExaminationSession.session_id == session_id,
            Examination.school_id == school_id,
        )
        .first()
    )
    return row if row is not None else (None, None)


def get_mark_in_school(db: Session, school_id: int, mark_id: int):
    return (
        db.query(Mark)
        .join(Student, Student.student_id == Mark.student_id)
        .filter(Mark.mark_id == mark_id, Student.school_id == school_id)
        .first()
    )


def get_marks_in_school(db: Session, school_id: int, mark_ids: list[int]) -> list[Mark]:
    if not mark_ids:
        return []
    return (
        db.query(Mark)
        .join(Student, Student.student_id == Mark.student_id)
        .filter(Mark.mark_id.in_(mark_ids), Student.school_id == school_id)
        .all()
    )
