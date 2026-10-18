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
    # Scope the read-back to legacy rows: session rows may share the same
    # (student, subject, term), and returning one would break the legacy
    # response contract (and show the wrong score).
    return db.query(Mark).filter(
        Mark.student_id == student_id, Mark.subject_id == subject_id,
        Mark.term == term, Mark.examination_session_id.is_(None),
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
        approved_at=func.now(),
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


def list_corrections(db: Session, mark_id: int) -> list[dict]:
    """Full audit trail for one mark: old/new score and grade, reason, who
    corrected it, who approved it (admin self-approval), and both server
    timestamps. Historical rows with NULL approved_at are returned as-is."""
    from common.models import User

    rows = (
        db.query(MarkCorrection)
        .filter(MarkCorrection.mark_id == mark_id)
        .order_by(MarkCorrection.correction_id)
        .all()
    )
    user_ids = sorted(
        {r for row in rows for r in (row.corrected_by, row.approved_by) if r is not None}
    )
    usernames = (
        {u.user_id: u.username for u in db.query(User).filter(User.user_id.in_(user_ids)).all()}
        if user_ids
        else {}
    )
    grade_ids = sorted(
        {g for row in rows for g in (row.old_grade_level_id, row.new_grade_level_id) if g is not None}
    )
    grade_labels = (
        {g.level_id: g.label for g in db.query(GradeLevel).filter(GradeLevel.level_id.in_(grade_ids)).all()}
        if grade_ids
        else {}
    )
    return [
        {
            "correction_id": row.correction_id,
            "old_score": row.old_score,
            "new_score": row.new_score,
            "old_grade": grade_labels.get(row.old_grade_level_id),
            "new_grade": grade_labels.get(row.new_grade_level_id),
            "reason": row.reason,
            "corrected_by": row.corrected_by,
            "corrected_by_name": usernames.get(row.corrected_by),
            "corrected_at": row.corrected_at,
            "approved_by": row.approved_by,
            "approved_by_name": usernames.get(row.approved_by),
            "approved_at": row.approved_at,
        }
        for row in rows
    ]


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


# ---- Results aggregation (Phase 6) ----
# Published, non-withheld rows only. Marks-wise scopes sum each row's own
# captured maximum (mixed maxima are valid: 42/50 + 78/100 = 120/150, never
# averaged percentages). Grade-wise scopes count distributions and never
# invent numbers. Absent/missing/exempt rows are listed, excluded from
# totals, and raise the incomplete flag. Legacy session-less rows group by
# verbatim term string under "earlier terms" and never merge with year
# scopes, so double-counting is impossible by construction.


def _subject_names(db: Session, school_id: int) -> dict:
    from common.models import Subject as SubjectModel

    return {
        s.subject_id: s.name
        for s in db.query(SubjectModel)
        .filter(SubjectModel.school_id == school_id)
        .all()
    }


def _grade_for_percentage(db: Session, scale_id: int | None, percentage: float):
    """A grade only when the pinned scale defines numeric bands. No global
    thresholds are invented here; without bands the answer is None."""
    if scale_id is None or percentage is None:
        return None
    levels = (
        db.query(GradeLevel)
        .filter(
            GradeLevel.scale_id == scale_id,
            GradeLevel.min_score.is_not(None),
            GradeLevel.max_score.is_not(None),
        )
        .order_by(GradeLevel.rank)
        .all()
    )
    for level in levels:
        if level.min_score <= percentage <= level.max_score:
            return {"label": level.label, "description": level.description}
    return None


def _visible_marks_query(db: Session, student_id: int):
    return db.query(Mark).filter(
        Mark.student_id == student_id,
        Mark.workflow_status == "published",
        Mark.is_withheld.is_(False),
    )


def _exam_context(db: Session, exam, sessions):
    from common.models import ExaminationType as TypeModel

    type_row = db.query(TypeModel).filter(TypeModel.type_id == exam.type_id).first()
    dates = sorted(s.date for s in sessions if s.date is not None)
    return {
        "exam_id": exam.exam_id,
        "name": exam.name,
        "start_date": dates[0].isoformat() if dates else None,
        "end_date": dates[-1].isoformat() if dates else None,
        "result_format": exam.result_format_expectation,
        "type_name": type_row.name if type_row else None,
        "status": exam.status,
    }


def _row_view(subject_names: dict, mark) -> dict:
    return {
        "mark_id": mark.mark_id,
        "student_id": mark.student_id,
        "subject_id": mark.subject_id,
        "subject_name": subject_names.get(mark.subject_id, f"Subject #{mark.subject_id}"),
        "score": mark.score,
        "max_marks": mark.max_marks,
        "grade_level_id": mark.grade_level_id,
        "grade": None,
        "assessment_status": mark.assessment_status,
        "workflow_status": mark.workflow_status,
        "is_withheld": mark.is_withheld,
        "term": mark.term,
        "examination_session_id": mark.examination_session_id,
    }


def _summarize_marks_rows(rows: list) -> dict:
    """Totals over present numeric rows; incomplete when anything else lurks.

    Incomplete means an assessment was not sat (absent/missing/exempt) or
    there is nothing to count at all — it is not a property of the format:
    a fully-graded grade-format exam is complete, while a marks exam with
    one absent row is not. Percentages are always over papers sat.
    """
    present = [m for m in rows if m.assessment_status == "present" and m.score is not None]
    obtained = sum(m.score for m in present)
    possible = sum(m.max_marks for m in present if m.max_marks)
    incomplete = (not rows) or any(m.assessment_status != "present" for m in rows)
    percentage = round(obtained / possible * 100, 2) if possible else None
    return {
        "obtained": obtained,
        "possible": possible,
        "percentage": percentage,
        "incomplete": incomplete,
        "subjects_counted": len(present),
        "subjects_total": len(rows),
    }


def student_results(db: Session, school_id: int, student_id: int,
                    year_id: int | None = None, exam_id: int | None = None) -> dict:
    from common.models import Examination as ExamModel

    names = _subject_names(db, school_id)
    session_rows = (
        _visible_marks_query(db, student_id)
        .filter(Mark.examination_session_id.is_not(None))
        .all()
    )
    legacy_rows = (
        _visible_marks_query(db, student_id)
        .filter(Mark.examination_session_id.is_(None))
        .order_by(Mark.term)
        .all()
    )
    examinations, aggregate_rows = [], []
    seen_exams: dict[int, dict] = {}
    sessions_by_exam: dict[int, list] = {}
    for mark in session_rows:
        session, exam = get_session_with_exam(db, mark.examination_session_id)
        if session is None or exam is None:
            continue
        if exam.school_id != school_id:
            continue
        if year_id is not None and exam.year_id != year_id:
            continue
        if exam_id is not None and exam.exam_id != exam_id:
            continue
        key = exam.exam_id
        if key not in seen_exams:
            seen_exams[key] = {"exam": exam, "sessions": [], "subjects": []}
            examinations.append(seen_exams[key])
        seen_exams[key]["sessions"].append(session)
        view = _row_view(names, mark)
        seen_exams[key]["subjects"].append(view)
        aggregate_rows.append(mark)
    final_examinations = []
    for holder in examinations:
        exam = holder["exam"]
        entry = _exam_context(db, exam, holder["sessions"])
        entry["subjects"] = holder["subjects"]
        final_examinations.append(entry)
    examinations = final_examinations
    by_exam_marks: dict[int, list] = {}
    for mark in aggregate_rows:
        by_exam_marks.setdefault(mark.examination_session_id, []).append(mark)
    exam_of_session = {}
    for holder in seen_exams.values():
        for s in holder["sessions"]:
            exam_of_session[s.session_id] = holder["exam"].exam_id
    marks_by_exam: dict[int, list] = {}
    for mark in aggregate_rows:
        marks_by_exam.setdefault(exam_of_session[mark.examination_session_id], []).append(mark)
    for entry in examinations:
        summary = _summarize_marks_rows(marks_by_exam.get(entry["exam_id"], []))
        entry.update(summary)
        if entry["result_format"] == "marks" and summary["percentage"] is not None:
            exam = db.query(ExamModel).filter(ExamModel.exam_id == entry["exam_id"]).first()
            grade = _grade_for_percentage(
                db, exam.grade_scale_id if exam else None, summary["percentage"])
            if grade:
                entry["grade"] = grade
        if entry["result_format"] == "grade":
            dist: dict[str, int] = {}
            for v in entry["subjects"]:
                if v["assessment_status"] == "present":
                    level = db.query(GradeLevel).filter(
                        GradeLevel.level_id == v["grade_level_id"]).first()
                    label = level.label if level else "Ungraded"
                    dist[label] = dist.get(label, 0) + 1
            entry["grade_distribution"] = dist
            # Grade scopes carry no numbers: drop the numeric summary keys
            # so callers cannot mistake them for computed totals.
            for numeric_key in ("obtained", "possible", "percentage", "subjects_counted"):
                entry.pop(numeric_key, None)
    legacy_terms = []
    by_term: dict[str, list] = {}
    for mark in legacy_rows:
        by_term.setdefault(mark.term, []).append(mark)
    for term, rows in sorted(by_term.items()):
        views = [_row_view(names, m) for m in rows]
        legacy_terms.append({"term": term, "subjects": views, **_summarize_marks_rows(rows)})
    aggregate = None
    marks_rows = [m for m in aggregate_rows if m.result_format == "marks"]
    if marks_rows:
        aggregate = _summarize_marks_rows(marks_rows)
        aggregate["scope"] = "year" if year_id is not None else "all examinations"
    elif aggregate_rows:
        aggregate = {"scope": "grade-only", "note": "Not applicable for grade-based assessment"}
    return {
        "student_id": student_id,
        "examinations": examinations,
        "legacy_terms": legacy_terms,
        "aggregate": aggregate,
    }


def examination_results(db: Session, school_id: int, exam, view: str = "published",
                        entry_session_ids: set[int] | None = None) -> dict:
    """One examination's results per student, plus the missing list.

    view="published" (default): published, non-withheld rows only — what
    parents and result consumers see. view="entry": every workflow state
    (draft/submitted/published), including withheld rows, so staff entry
    grids show work in progress instead of hiding it; "missing" then means
    no row of any kind for a class student. Both views are staff-gated at
    the router; parents never reach this endpoint.

    entry_session_ids restricts an entry view to specific sessions (the
    teacher case): only those sessions contribute rows and the missing
    list, so a teacher never receives other classes' drafts. None (admins,
    masters) keeps the whole examination.
    """
    if view not in ("published", "entry"):
        raise AppError("view must be 'published' or 'entry'", status_code=400)
    entry_view = view == "entry"
    session_q = db.query(ExaminationSession).filter(
        ExaminationSession.exam_id == exam.exam_id
    )
    if entry_session_ids is not None:
        session_q = session_q.filter(
            ExaminationSession.session_id.in_(entry_session_ids)
        )
    sessions = session_q.all()
    names = _subject_names(db, school_id)
    students: dict[int, dict] = {}
    for session in sessions:
        row_q = db.query(Mark).filter(
            Mark.examination_session_id == session.session_id,
        )
        if not entry_view:
            row_q = row_q.filter(
                Mark.workflow_status == "published",
                Mark.is_withheld.is_(False),
            )
        for mark in row_q.all():
            entry = students.setdefault(mark.student_id, {"student_id": mark.student_id, "subjects": []})
            entry["subjects"].append(_row_view(names, mark))
    class_ids = sorted({s.class_id for s in sessions})
    session_ids = {s.session_id for s in sessions}
    missing = []
    if class_ids:
        seen_q = db.query(Mark.student_id).filter(
            Mark.examination_session_id.in_(session_ids),
        )
        if not entry_view:
            seen_q = seen_q.filter(
                Mark.workflow_status == "published",
                Mark.is_withheld.is_(False),
            )
        seen_here = {m.student_id for m in seen_q.all()}
        for student in (
            db.query(Student)
            .filter(Student.class_id.in_(class_ids), Student.is_active.is_(True))
            .all()
        ):
            if student.student_id not in seen_here:
                missing.append(student.student_id)
    results = []
    for student_id, entry in sorted(students.items()):
        marks_q = db.query(Mark).filter(
            Mark.student_id == student_id,
            Mark.examination_session_id.in_(session_ids),
        )
        if not entry_view:
            marks_q = marks_q.filter(
                Mark.workflow_status == "published",
                Mark.is_withheld.is_(False),
            )
        entry.update(_summarize_marks_rows(marks_q.all()))
        results.append(entry)
    return {
        "exam_id": exam.exam_id,
        "name": exam.name,
        "status": exam.status,
        "results": results,
        "missing_student_ids": sorted(missing),
    }


def teacher_session_ids_for_exam(db: Session, staff_id: int, exam_id: int) -> set[int]:
    """Session slots within an exam that this teacher teaches — usually a
    subset of the exam's sessions. Scopes the teacher entry view so another
    class's in-flight drafts never leave the server for a co-teacher."""
    from common.models import ExaminationSession as SessionModel

    triples = {
        (c, s)
        for c, s in db.query(
            TeacherClassSubject.class_id, TeacherClassSubject.subject_id
        ).filter(TeacherClassSubject.staff_id == staff_id).all()
    }
    if not triples:
        return set()
    return {
        sl.session_id
        for sl in db.query(SessionModel).filter(SessionModel.exam_id == exam_id).all()
        if (sl.class_id, sl.subject_id) in triples
    }


def teacher_in_exam(db: Session, staff_id: int, exam_id: int) -> bool:
    """True when the teacher is assigned to at least one session slot."""
    return bool(teacher_session_ids_for_exam(db, staff_id, exam_id))
