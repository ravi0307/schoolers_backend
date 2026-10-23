from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from common.database import get_db
from common.authz import is_master_like
from common.authz import is_master_like, is_school_admin
from common.dependencies import require_role, require_examinations_enabled, CurrentUser
from common.exceptions import AppError, ForbiddenError, NotFoundError
import bulk
import repository as repo
from schemas import (
    MarkUpsert, MarkRead, SessionMarkCreate, SessionMarkUpdate,
    WithholdPayload, UnwithholdPayload, CorrectMarkPayload, MarkReadV2,
    StudentResultsRead, ExaminationResultsRead, CorrectionRead,
)

router = APIRouter(prefix="/marks", tags=["marks"])


def _master_scope(current_user: CurrentUser, school_id: int | None) -> int:
    """Master has no implicit school: every read must name one explicitly.

    Reads leave no audit trail anywhere in the system (audit covers writes
    via modified_by/at), so the explicit school_id parameter is the control:
    it forces the scope into the request instead of an ambient default.
    """
    if school_id is None:
        raise ForbiddenError("Master access requires an explicit school_id")
    return school_id


@router.get("/student/{student_id}", response_model=list[MarkRead])
def get_student_marks(
    student_id: int,
    school_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin", "master")),
):
    # Phase 0 existence-oracle fix: no unscoped existence probe runs before
    # these checks. Parent/teacher linkage checks are uniform (False for
    # unknown ids too → Forbidden); admin/master school checks return a
    # uniform NotFoundError for unknown and out-of-school ids alike, so
    # callers cannot distinguish "missing" from "another school's".
    # Non-master callers' school_id argument, if supplied, is ignored: the
    # token's school always wins.
    scope = current_user.school_id
    if is_master_like(current_user.role):
        scope = _master_scope(current_user, school_id)
        if not repo.student_in_school(db, student_id, scope):
            raise NotFoundError("Student not found")
    elif current_user.role == "parent":
        if not current_user.linked_person_id or not repo.is_parent_of(
            db, current_user.linked_person_id, student_id
        ):
            raise ForbiddenError("You can only view marks for your own children")
    elif current_user.role == "teacher":
        if not current_user.linked_person_id or not repo.teacher_teaches_student(
            db, current_user.linked_person_id, student_id
        ):
            raise ForbiddenError("You can only view marks for students you teach")
    elif not repo.student_in_school(db, student_id, current_user.school_id):
        raise NotFoundError("Student not found")

    # Parents see published results only, never drafts and never withheld
    # rows; staff views keep everything so drafts carry visible badges.
    if current_user.role == "parent":
        rows = repo.get_for_student(db, student_id, published_only=True, include_withheld=False)
    else:
        rows = repo.get_for_student(db, student_id)
    # Read-side metadata (subject names, scale-resolved grades) rides the
    # same rows; authorization above is untouched.
    return repo.mark_read_views(db, scope, rows)


@router.get("/class/{class_id}", response_model=list[MarkRead])
def get_class_marks(
    class_id: int,
    term: str | None = None,
    school_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin", "master")),
):
    # The class must belong to the caller's school before any marks are read.
    # Master names the school explicitly; everyone else is bound to the token.
    if is_master_like(current_user.role):
        scope = _master_scope(current_user, school_id)
        if not repo.class_in_school(db, class_id, scope):
            raise NotFoundError("Class not found")
    elif current_user.school_id is not None and not repo.class_in_school(
        db, class_id, current_user.school_id
    ):
        raise NotFoundError("Class not found")

    # A teacher only sees (and can therefore only edit) marks for the
    # subjects they're actually assigned to teach in this class.
    subject_ids = None
    if current_user.role == "teacher":
        if not current_user.linked_person_id:
            raise ForbiddenError("This teacher account isn't linked to a teacher record")
        subject_ids = repo.get_subject_ids_for_class(db, current_user.linked_person_id, class_id)
    return repo.get_for_class(db, class_id, subject_ids, term)


@router.put("/{student_id}/{subject_id}", response_model=MarkRead)
def upsert_mark(
    student_id: int,
    subject_id: int,
    payload: MarkUpsert,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    # Phase 0 existence-oracle fix: the old unscoped student_exists probe is
    # gone. The subject check below is school-scoped (uniform 404 for unknown
    # and out-of-school subjects alike); the teacher linkage check is uniform
    # (False for unknown students too → Forbidden); the admin school check
    # returns a uniform NotFoundError. Master keeps no write access (403).
    if not repo.subject_exists(db, subject_id, current_user.school_id):
        raise NotFoundError("Subject not found")

    # Admin can grade anything in their own school; a teacher only what
    # they're assigned to teach.
    if current_user.role == "teacher":
        if not current_user.linked_person_id:
            raise ForbiddenError("This teacher account isn't linked to a teacher record")
        if not repo.teacher_can_grade(db, current_user.linked_person_id, student_id, subject_id):
            raise ForbiddenError("You don't teach this subject in this student's class")
    elif not repo.student_in_school(db, student_id, current_user.school_id):
        raise NotFoundError("Student not found")
    updated_by = current_user.linked_person_id if current_user.role == "teacher" else None
    return repo.upsert_mark(
        db, student_id, subject_id, payload.term, payload.score,
        updated_by, updated_by_user=current_user.user_id,
    )


# ---- Session marks lifecycle (Phase 4) ----


def _session_and_exam(db: Session, school_id: int, session_id: int):
    session, exam = repo.get_session_in_school(db, school_id, session_id)
    if session is None or exam is None:
        raise NotFoundError("Examination session not found")
    return session, exam


def _require_session_grader(db: Session, current_user: CurrentUser, session) -> None:
    """Teacher assigned to the session's class+subject, or own-school admin."""
    if current_user.role == "teacher":
        if not current_user.linked_person_id or not repo.teacher_assigned(
            db, current_user.linked_person_id, session.class_id, session.subject_id
        ):
            raise ForbiddenError("You don't teach this examination session")


@router.post("/session-marks", response_model=MarkReadV2, status_code=201)
def create_session_mark(
    payload: SessionMarkCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    require_examinations_enabled(db, current_user.school_id)
    session, exam = _session_and_exam(db, current_user.school_id, payload.session_id)
    _require_session_grader(db, current_user, session)
    return repo.create_session_mark(
        db, session, exam, payload.student_id, payload.subject_id,
        payload.model_dump(exclude_unset=True), current_user.user_id,
    )


@router.patch("/{mark_id}", response_model=MarkReadV2)
def update_mark(
    mark_id: int,
    payload: SessionMarkUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    require_examinations_enabled(db, current_user.school_id)
    mark = repo.get_mark_in_school(db, current_user.school_id, mark_id)
    if mark is None:
        raise NotFoundError("Mark not found")
    if mark.examination_session_id is None:
        raise ForbiddenError("Term marks use PUT /marks/{student_id}/{subject_id}")
    session, _ = _session_and_exam(db, current_user.school_id, mark.examination_session_id)
    _require_session_grader(db, current_user, session)
    return repo.update_session_mark(db, mark, payload.model_dump(exclude_unset=True))


@router.post("/sessions/{session_id}/submit")
def submit_session_marks(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    require_examinations_enabled(db, current_user.school_id)
    session, _ = _session_and_exam(db, current_user.school_id, session_id)
    _require_session_grader(db, current_user, session)
    return repo.submit_session_marks(db, session)


@router.post("/sessions/{session_id}/publish")
def publish_session_marks(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    # Publishing is admin-only; require_school_scope is folded into the
    # session lookup so cross-school ids are a uniform 404.
    require_examinations_enabled(db, current_user.school_id)
    if current_user.school_id is None:
        raise ForbiddenError("User is not associated with a school")
    session, _ = _session_and_exam(db, current_user.school_id, session_id)
    return repo.publish_session_marks(db, session)


@router.post("/withhold")
def withhold_marks(
    payload: WithholdPayload,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    require_examinations_enabled(db, current_user.school_id)
    if current_user.school_id is None:
        raise ForbiddenError("User is not associated with a school")
    rows = repo.get_marks_in_school(db, current_user.school_id, payload.mark_ids)
    if len(rows) != len(set(payload.mark_ids)):
        raise NotFoundError("Mark not found")
    return repo.withhold_marks(db, [m.mark_id for m in rows], payload.reason, current_user.user_id)


@router.post("/unwithhold")
def unwithhold_marks(
    payload: UnwithholdPayload,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    require_examinations_enabled(db, current_user.school_id)
    if current_user.school_id is None:
        raise ForbiddenError("User is not associated with a school")
    rows = repo.get_marks_in_school(db, current_user.school_id, payload.mark_ids)
    if len(rows) != len(set(payload.mark_ids)):
        raise NotFoundError("Mark not found")
    return repo.unwithhold_marks(db, [m.mark_id for m in rows])


@router.post("/{mark_id}/correct", response_model=MarkReadV2)
def correct_mark(
    mark_id: int,
    payload: CorrectMarkPayload,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    require_examinations_enabled(db, current_user.school_id)
    if current_user.school_id is None:
        raise ForbiddenError("User is not associated with a school")
    mark = repo.get_mark_in_school(db, current_user.school_id, mark_id)
    if mark is None:
        raise NotFoundError("Mark not found")
    if mark.examination_session_id is None:
        raise ForbiddenError("Term marks use PUT /marks/{student_id}/{subject_id}")
    data = payload.model_dump(exclude_unset=True)
    mark, _ = repo.correct_mark(db, mark, data, payload.reason, current_user.user_id)
    return mark


@router.get("/{mark_id}/corrections", response_model=list[CorrectionRead])
def list_mark_corrections(
    mark_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("admin")),
):
    """Audit trail for one mark: old/new values, reason, correcting admin,
    approval timestamp. Admin-only and school-scoped — same audience as
    the correction action itself (self-approval model, unchanged)."""
    require_examinations_enabled(db, current_user.school_id)
    if current_user.school_id is None:
        raise ForbiddenError("User is not associated with a school")
    mark = repo.get_mark_in_school(db, current_user.school_id, mark_id)
    if mark is None:
        raise NotFoundError("Mark not found")
    return repo.list_corrections(db, mark.mark_id)


# ---- Bulk CSV import/export (Phase 4) ----


@router.get("/import/template", response_class=PlainTextResponse)
def import_template(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    """Starter CSV for a session: one row per active class student."""
    require_examinations_enabled(db, current_user.school_id)
    if current_user.school_id is None:
        raise ForbiddenError("User is not associated with a school")
    session, _ = _session_and_exam(db, current_user.school_id, session_id)
    _require_session_grader(db, current_user, session)
    return PlainTextResponse(
        bulk.render_template(db, session),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=marks-session-{session_id}.csv"},
    )


@router.post("/import")
def preview_import(
    session_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    """Validate a spreadsheet, write a preview batch, commit nothing."""
    require_examinations_enabled(db, current_user.school_id)
    if current_user.school_id is None:
        raise ForbiddenError("User is not associated with a school")
    session, exam = _session_and_exam(db, current_user.school_id, session_id)
    _require_session_grader(db, current_user, session)
    batch, receipt = bulk.preview_upload(
        db, current_user.school_id, session, exam,
        file.filename or "upload.csv", file.file.read(), current_user.user_id,
    )
    return {
        "batch_id": batch.batch_id,
        "status": batch.status,
        "rows": receipt,
        "summary": {
            verdict: sum(1 for r in receipt if r["verdict"] == verdict)
            for verdict in ("valid", "invalid", "warning")
        },
    }


@router.post("/import/{batch_id}/commit")
def commit_import(
    batch_id: int,
    apply_partial: bool = False,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    """Commit a previewed batch. All-or-nothing unless an admin explicitly
    opts into partial application; invalid rows never write either way."""
    batch = (
        db.query(bulk.MarkImportBatch)
        .filter(bulk.MarkImportBatch.batch_id == batch_id)
        .first()
    )
    if current_user.school_id is None:
        raise ForbiddenError("User is not associated with a school")
    if batch is None or batch.school_id != current_user.school_id:
        raise NotFoundError("Import batch not found")
    require_examinations_enabled(db, batch.school_id)
    session, _ = _session_and_exam(db, batch.school_id, batch.session_id)
    _require_session_grader(db, current_user, session)
    return bulk.commit_batch(
        db, batch, apply_partial, is_admin=is_school_admin(current_user.role)
    )


@router.get("/export", response_class=PlainTextResponse)
def export_marks(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin")),
):
    """Authorized CSV export for one session, formula-injection guarded."""
    require_examinations_enabled(db, current_user.school_id)
    if current_user.school_id is None:
        raise ForbiddenError("User is not associated with a school")
    session, _ = _session_and_exam(db, current_user.school_id, session_id)
    _require_session_grader(db, current_user, session)
    rows = repo.get_for_class(db, session.class_id)
    rows = [m for m in rows if m.examination_session_id == session_id]
    return PlainTextResponse(
        bulk.export_session_csv(db, session, rows),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=marks-session-{session_id}.csv"},
    )


# ---- Results aggregation (Phase 6) ----


@router.get("/results/student/{student_id}", response_model=StudentResultsRead)
def get_student_results(
    student_id: int,
    year_id: int | None = None,
    exam_id: int | None = None,
    school_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("parent", "teacher", "admin", "master")),
    period_id: int | None = None,
):
    """Published, non-withheld results grouped by examination, plus legacy
    terms and a year aggregate. Scoping mirrors the marks reads; parents
    and students never see drafts, withheld rows, or other families."""
    if is_master_like(current_user.role):
        scope = _master_scope(current_user, school_id)
        if not repo.student_in_school(db, student_id, scope):
            raise NotFoundError("Student not found")
    elif current_user.role == "parent":
        if not current_user.linked_person_id or not repo.is_parent_of(
            db, current_user.linked_person_id, student_id
        ):
            raise ForbiddenError("You can only view results for your own children")
        scope = current_user.school_id
    elif current_user.role == "teacher":
        if not current_user.linked_person_id or not repo.teacher_teaches_student(
            db, current_user.linked_person_id, student_id
        ):
            raise ForbiddenError("You can only view results for students you teach")
        scope = current_user.school_id
    elif not repo.student_in_school(db, student_id, current_user.school_id):
        raise NotFoundError("Student not found")
    else:
        scope = current_user.school_id
    require_examinations_enabled(db, scope)
    # period_id sits after current_user so existing direct-call sites keep
    # their positional order; FastAPI reads it as an ordinary query param.
    return repo.student_results(db, scope, student_id, year_id, exam_id, period_id=period_id)


@router.get("/results/examination/{exam_id}", response_model=ExaminationResultsRead)
def get_examination_results(
    exam_id: int,
    school_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_role("teacher", "admin", "master")),
    view: str = "published",
):
    """One examination's results per student plus the missing list.
    view=published (default): published, non-withheld rows. view=entry:
    every workflow state incl. withheld — staff entry grids only. Teachers
    see examinations they teach in; parents use the student view instead."""
    from common.models import Examination as ExamModel

    if view not in ("published", "entry"):
        raise AppError("view must be 'published' or 'entry'", status_code=400)
    if is_master_like(current_user.role):
        scope = _master_scope(current_user, school_id)
    else:
        scope = current_user.school_id
    require_examinations_enabled(db, scope)
    exam = db.query(ExamModel).filter(
        ExamModel.exam_id == exam_id, ExamModel.school_id == scope).first()
    if exam is None:
        raise NotFoundError("Examination not found")
    if current_user.role == "teacher":
        if not current_user.linked_person_id or not repo.teacher_in_exam(
            db, current_user.linked_person_id, exam_id
        ):
            raise ForbiddenError("You don't teach in this examination")
    # Teachers only ever receive entry-view rows for sessions they teach;
    # admins and masters see the whole examination.
    entry_session_ids = None
    if view == "entry" and current_user.role == "teacher":
        entry_session_ids = repo.teacher_session_ids_for_exam(
            db, current_user.linked_person_id, exam_id
        )
    return repo.examination_results(
        db, scope, exam, view=view, entry_session_ids=entry_session_ids
    )
