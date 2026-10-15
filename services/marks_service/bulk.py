"""CSV bulk marks import/export (Phase 4).

CSV first, stdlib only: no new dependency and no macro/formula execution
surface on import. XLSX is deferred by plan. Uploads are size-limited,
school-scoped, and validated row by row with a preview stage; nothing
writes until commit, and commit is atomic unless an admin explicitly opts
into partial application.
"""
import csv
import hashlib
import io

from sqlalchemy.orm import Session

from common.models import (
    AcademicPeriod, Examination, ExaminationSession, Mark, MarkImportBatch,
    MarkImportRow, Student,
)
from common.exceptions import AppError, ConflictError, NotFoundError
import repository as repo

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
TEMPLATE_COLUMNS = [
    "student_id", "admission_no", "student_name",
    "score_or_grade", "assessment_status",
]
ASSESSMENT_CHOICES = ("present", "absent", "missing", "exempt")


def safe_cell(value) -> str:
    """Neutralize spreadsheet formula injection in exports."""
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r", "\n"):
        return "'" + text
    return text


def _scope_hash(session_id: int) -> str:
    return hashlib.sha256(f"session:{session_id}".encode()).hexdigest()


def render_template(db: Session, session) -> str:
    """Starter CSV for a session: one row per active class student."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(TEMPLATE_COLUMNS)
    students = (
        db.query(Student)
        .filter(Student.class_id == session.class_id, Student.is_active.is_(True))
        .order_by(Student.name)
        .all()
    )
    for student in students:
        writer.writerow([student.student_id, student.admission_no, student.name, "", "present"])
    return buf.getvalue()


def parse_csv(text: str) -> tuple[list[dict], list[str]]:
    """Parse upload text. Returns (rows, fatal_errors)."""
    try:
        reader = csv.DictReader(io.StringIO(text))
    except csv.Error as error:
        return [], [f"Unparseable CSV: {error}"]
    if reader.fieldnames != TEMPLATE_COLUMNS:
        return [], [
            "Unexpected columns: expected "
            + ",".join(TEMPLATE_COLUMNS)
            + ". Download a fresh template."
        ]
    return list(reader), []


def _validate_row(db: Session, session, exam, row_no: int, row: dict):
    """Returns (verdict, resolved, errors) where resolved holds final mark
    columns (score/max_marks/grade_level_id/result_format/term). Never raises
    for row problems."""
    errors = []
    try:
        student_id = int(str(row.get("student_id") or "").strip())
    except (TypeError, ValueError):
        return "invalid", None, ["student_id must be an integer"]
    student = db.query(Student).filter(Student.student_id == student_id).first()
    if student is None or student.class_id != session.class_id or not student.is_active:
        return "invalid", None, ["student is not in the session's class"]
    admission = str(row.get("admission_no") or "").strip()
    if admission and admission != (student.admission_no or ""):
        return "invalid", None, ["admission_no does not match student_id"]
    status = str(row.get("assessment_status") or "present").strip().lower()
    if status not in ASSESSMENT_CHOICES:
        return "invalid", None, [f"assessment_status must be one of {', '.join(ASSESSMENT_CHOICES)}"]
    raw_value = str(row.get("score_or_grade") or "").strip()
    term = "Term 1"
    if exam.period_id is not None:
        period = db.query(AcademicPeriod).filter(
            AcademicPeriod.period_id == exam.period_id).first()
        if period is not None:
            term = period.name
    if status != "present":
        if raw_value:
            return "invalid", None, [f"a '{status}' row must leave score_or_grade empty"]
        return "valid", {
            "student_id": student_id, "assessment_status": status,
            "score": None, "max_marks": None, "grade_level_id": None,
            "grade_label": None, "result_format": exam.result_format_expectation,
            "term": term,
        }, []
    if exam.result_format_expectation == "marks":
        if not raw_value:
            return "invalid", None, ["score required for a present marks entry"]
        try:
            score = int(raw_value)
        except ValueError:
            return "invalid", None, ["score must be a whole number"]
        if score < 0 or score > session.max_marks:
            return "invalid", None, [f"score must be between 0 and {session.max_marks}"]
        return "valid", {
            "student_id": student_id, "assessment_status": "present",
            "score": score, "max_marks": session.max_marks, "grade_level_id": None,
            "grade_label": None, "result_format": "marks", "term": term,
        }, []
    if not raw_value:
        return "invalid", None, ["grade required for a present grade entry"]
    level = repo.resolve_grade_level(db, exam.grade_scale_id, raw_value)
    if level is None:
        return "invalid", None, [f"grade '{raw_value}' is not in this examination's scale"]
    return "valid", {
        "student_id": student_id, "assessment_status": "present",
        "score": None, "max_marks": None, "grade_level_id": level.level_id,
        "grade_label": raw_value, "result_format": "grade", "term": term,
    }, []


def preview_upload(
    db: Session, school_id: int, session, exam, filename: str,
    content: bytes, user_id: int | None,
):
    """Validate everything, write a preview batch, commit nothing to marks."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise AppError("File exceeds the 2 MB upload limit", status_code=400)
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise AppError("File must be UTF-8 encoded CSV", status_code=400)
    rows, fatal = parse_csv(text)
    if fatal:
        raise AppError("; ".join(fatal), status_code=400)
    file_hash = hashlib.sha256(content).hexdigest()
    scope_hash = _scope_hash(session.session_id)
    existing = (
        db.query(MarkImportBatch)
        .filter(
            MarkImportBatch.school_id == school_id,
            MarkImportBatch.file_hash == file_hash,
            MarkImportBatch.scope_hash == scope_hash,
        )
        .first()
    )
    if existing is not None:
        # Idempotent retry: hand back the prior receipt, write nothing new.
        return existing, [
            {"row_no": r.row_no, "verdict": r.verdict, "errors": r.errors or []}
            for r in db.query(MarkImportRow)
            .filter(MarkImportRow.batch_id == existing.batch_id)
            .order_by(MarkImportRow.row_no)
            .all()
        ]
    if session.status in ("cancelled", "completed"):
        raise ConflictError(f"Session is {session.status}; imports are closed")
    batch = MarkImportBatch(
        school_id=school_id, uploaded_by=user_id, source_filename=filename,
        file_hash=file_hash, scope_hash=scope_hash,
        session_id=session.session_id, status="preview",
    )
    db.add(batch)
    db.flush()
    seen: set[int] = set()
    receipt = []
    for idx, row in enumerate(rows, start=1):
        verdict, normalized, errors = _validate_row(db, session, exam, idx, row)
        if verdict == "valid":
            sid = normalized["student_id"]
            if sid in seen:
                verdict, normalized, errors = "invalid", None, ["duplicate student in file"]
            else:
                seen.add(sid)
                clash = (
                    db.query(Mark)
                    .filter(
                        Mark.examination_session_id == session.session_id,
                        Mark.student_id == sid,
                    )
                    .first()
                )
                if clash is not None and clash.workflow_status != "draft":
                    verdict, normalized, errors = (
                        "invalid", None, ["a submitted or published mark already exists"]
                    )
        db.add(MarkImportRow(
            batch_id=batch.batch_id, row_no=idx,
            student_id=(normalized or {}).get("student_id"),
            subject_id=session.subject_id,
            payload={**(normalized or {}), "_row": idx},
            verdict=verdict, errors=errors,
        ))
        receipt.append({"row_no": idx, "verdict": verdict, "errors": errors})
    db.commit()
    db.refresh(batch)
    return batch, receipt


def commit_batch(db: Session, batch, apply_partial: bool, is_admin: bool):
    """Apply a previewed batch. Default is all-or-nothing; partial needs an
    explicit admin opt-in and still skips every invalid row with a receipt.
    Rows are re-validated against the LIVE session (maxima may have moved
    since preview); newly invalid rows are skipped, never forced."""
    if batch.status == "committed":
        return {"applied": 0, "updated_drafts": 0, "skipped": 0, "duplicate_receipt": True}
    if batch.status != "preview":
        raise ConflictError(f"Batch is {batch.status}; only previewed batches commit")
    rows = (
        db.query(MarkImportRow)
        .filter(MarkImportRow.batch_id == batch.batch_id)
        .order_by(MarkImportRow.row_no)
        .all()
    )
    invalid = [r for r in rows if r.verdict != "valid"]
    if invalid and not apply_partial:
        raise ConflictError(
            f"{len(invalid)} invalid rows: fix the file or retry with explicit partial application"
        )
    if apply_partial and not is_admin:
        raise AppError("Partial application requires an admin", status_code=403)
    session = (
        db.query(ExaminationSession)
        .filter(ExaminationSession.session_id == batch.session_id)
        .first()
    )
    if session is None or session.status in ("cancelled", "completed"):
        raise ConflictError("Session is closed; the batch cannot commit")
    exam = db.query(Examination).filter(Examination.exam_id == session.exam_id).first()
    applied = updated = skipped = 0
    for row in rows:
        if row.verdict != "valid":
            skipped += 1
            continue
        cols = dict(row.payload or {})
        cols.pop("_row", None)
        if cols.get("result_format") == "marks" and cols.get("score") is not None:
            if cols["score"] > session.max_marks:
                row.verdict = "invalid"
                row.errors = [f"score exceeds the current session maximum ({session.max_marks})"]
                skipped += 1
                continue
        if cols.get("result_format") == "grade" and cols.get("grade_label"):
            if repo.resolve_grade_level(db, exam.grade_scale_id, cols["grade_label"]) is None:
                row.verdict = "invalid"
                row.errors = ["grade is no longer in this examination's scale"]
                skipped += 1
                continue
        existing = (
            db.query(Mark)
            .filter(
                Mark.examination_session_id == batch.session_id,
                Mark.student_id == row.student_id,
            )
            .first()
        )
        if existing is not None:
            # Preview guaranteed draft-only here; refresh it in place.
            existing.assessment_status = cols.get("assessment_status", "present")
            existing.score = cols.get("score")
            existing.max_marks = cols.get("max_marks")
            existing.grade_level_id = cols.get("grade_level_id")
            existing.result_format = cols.get("result_format", "marks")
            existing.term = cols.get("term", existing.term)
            updated += 1
        else:
            db.add(Mark(
                student_id=row.student_id,
                subject_id=row.subject_id,
                term=cols.get("term") or "Term 1",
                examination_session_id=batch.session_id,
                workflow_status="draft",
                is_withheld=False,
                assessment_status=cols.get("assessment_status", "present"),
                score=cols.get("score"),
                max_marks=cols.get("max_marks"),
                grade_level_id=cols.get("grade_level_id"),
                result_format=cols.get("result_format", "marks"),
            ))
            applied += 1
    batch.status = "committed"
    db.commit()
    return {"applied": applied, "updated_drafts": updated, "skipped": skipped,
            "duplicate_receipt": False}


def export_session_csv(db: Session, session, rows) -> str:
    """Authorized export with formula-injection guards on every cell."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "student_id", "admission_no", "student_name", "subject_id",
        "score", "grade_level_id", "max_marks", "assessment_status",
        "workflow_status", "is_withheld",
    ])
    students = {
        s.student_id: s
        for s in db.query(Student)
        .filter(Student.student_id.in_([m.student_id for m in rows]))
        .all()
    } if rows else {}
    for mark in rows:
        student = students.get(mark.student_id)
        writer.writerow([
            safe_cell(mark.student_id),
            safe_cell(getattr(student, "admission_no", "")),
            safe_cell(getattr(student, "name", "")),
            safe_cell(mark.subject_id),
            safe_cell(mark.score if mark.score is not None else ""),
            safe_cell(mark.grade_level_id if mark.grade_level_id is not None else ""),
            safe_cell(mark.max_marks if mark.max_marks is not None else ""),
            safe_cell(mark.assessment_status),
            safe_cell(mark.workflow_status),
            safe_cell(mark.is_withheld),
        ])
    _ = session
    return buf.getvalue()
