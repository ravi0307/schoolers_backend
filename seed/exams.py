"""Idempotent Blue Horizon Academy examination fixtures (development only).

Seeds the examination workflow *state* the reset+seed flow cannot create while
the examinations feature flag is off: an academic year and periods, an
examination type, a grade scale, draft/published examinations with sessions,
session marks across every workflow and assessment status, a published-mark
correction, a withheld mark, class/subject enrollment rows, and CSV import
fixture files plus one previewed import batch.

Design rules (hard):
- Insert-if-missing by natural key; re-running never duplicates and never
  deletes or rewrites. No truncation, no resets.
- Never touches users, passwords, or sessions of users.
- Never flips schools.examinations_enabled (asserted false at the end).
- Scoped to Blue Horizon Academy only; other tenants are read-only.
- Direct DB writes are used only because every router that would create this
  state is gated by require_examinations_enabled (see services/*_service/
  router.py); marks workflow moves go through the real service repository
  functions so all validation is the product's own.

Provenance of every configured value (approved test fixtures, cited verbatim;
nothing here invents policy, thresholds, or historical dates):
- Academic year "2026-27", 2026-06-01..2027-05-31: tests/test_marks_import.py
- Periods "Term 1" (2026-06-01..2026-10-15, inactive) and "Term 2"
  (2026-10-16..2027-03-31, active): tests/test_marks_results.py
- Examination type "Half-Yearly", default max 100: tests/test_marks_import.py
- Grade scale "Alpha" v1 with A (80..100) and B (60..79):
  tests/test_marks_results.py — labels/ranks/min-max only; no thresholds or
  descriptions are invented beyond those fixtures.
- Session date 2026-12-15, 09:00-12:00, venue "Hall A", max 100:
  tests/test_marks_import.py and tests/test_examination_scheduling.py
  (_session_data); the other fixture sessions use sibling dates in the same
  fixture week (2026-12-16..18) so timetable/term semantics stay coherent.
- Examination names "Half-Yearly" (draft) and "Term 2 Weekly" (published,
  period-linked): tests/test_marks_import.py and tests/test_marks_results.py.
- Absent/missing/exempt entries carry no score or grade, per
  services/marks_service/repository.py:validate_session_payload (product rule,
  not a fixture invention): a zero can never masquerade as an absence.

Known product gap (documented, not fabricated): the import pipeline counts
"warning" rows (services/marks_service/router.py summary) and the admin UI
renders them, but no row rule in services/marks_service/bulk.py can currently
produce a warning verdict, so no warning CSV fixture is generated here.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from common.models import (
    AcademicPeriod,
    AcademicYear,
    ClassEnrollment,
    Examination,
    ExaminationSession,
    ExaminationType,
    GradeLevel,
    GradeScale,
    Mark,
    MarkCorrection,
    MarkImportBatch,
    School,
    SchoolClass,
    Student,
    StudentSubject,
    Subject,
    User,
)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHOOL_NAME = "Blue Horizon Academy"
CLASS_NAME = "Grade I"
ADMIN_USERNAME = "blue.horizon"

YEAR_NAME = "2026-27"
YEAR_START = date(2026, 6, 1)
YEAR_END = date(2027, 5, 31)

PERIOD_TERM1 = ("Term 1", date(2026, 6, 1), date(2026, 10, 15), False)
PERIOD_TERM2 = ("Term 2", date(2026, 10, 16), date(2027, 3, 31), True)

EXAM_TYPE_NAME = "Half-Yearly"
EXAM_TYPE_MAX = 100

SCALE_NAME = "Alpha"
SCALE_LEVELS = [
    # label, rank, min_score, max_score  (tests/test_marks_results.py)
    ("A", 1, 80, 100),
    ("B", 2, 60, 79),
]

EXAM_HY = "Half-Yearly"          # draft, no period -> marks term "Term 1"
EXAM_T2 = "Term 2 Weekly"        # published, Term 2 period -> term "Term 2"

SESSION_SUBJECTS = {
    EXAM_HY: ["Mathematics", "Hindi"],
    EXAM_T2: ["English", "Science"],
}
SESSION_DATES = {
    (EXAM_HY, "Mathematics"): date(2026, 12, 15),
    (EXAM_T2, "English"): date(2026, 12, 16),
    (EXAM_T2, "Science"): date(2026, 12, 17),
    (EXAM_HY, "Hindi"): date(2026, 12, 18),   # CSV target session (no marks)
}
SESSION_START = time(9, 0)
SESSION_END = time(12, 0)
SESSION_VENUE = "Hall A"
SESSION_MAX = 100

# Deterministic demo scores (data, not policy; each <= SESSION_MAX).
SCORE_ENGLISH = {"Aadhya Rao": 67, "Anaya Reddy": 75, "Kiara Kapoor": 50}
SCORE_SCIENCE = {"Aadhya Rao": 80}
CORRECTION_SCORE_OLD_NEW = (67, 72)   # Aadhya Rao, English, published
CORRECTION_REASON = "Answer script re-evaluated during verification"
WITHHOLD_STUDENT_NAME = "Anaya Reddy"
WITHHOLD_REASON = "Pending verification documents"
LATE_DRAFT_ASSESSMENT = "exempt"      # Kiara Kapoor, Mathematics, draft
VALID_CSV_SCORE = 60


def log(msg: str) -> None:
    print(f"[seed-exams] {msg}", flush=True)


@dataclass
class Summary:
    created: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)

    def note_created(self, what: str) -> None:
        self.created.append(what)
        log(f"created  {what}")

    def note_existing(self, what: str) -> None:
        self.existing.append(what)
        log(f"exists   {what}")

    def note(self, msg: str) -> None:
        self.actions.append(msg)
        log(msg)


def _load_bulk():
    """Import services/marks_service/bulk with its flat-import convention."""
    marks_dir = str(ROOT / "services" / "marks_service")
    inserted = marks_dir not in sys.path
    if inserted:
        sys.path.insert(0, marks_dir)
    try:
        for mod in ("repository", "schemas", "bulk"):
            sys.modules.pop(mod, None)
        from services.marks_service import bulk as bulk_mod
        return bulk_mod
    finally:
        if inserted:
            sys.path.remove(marks_dir)


def _get_or_create(db: Session, model, key: dict, defaults: dict, summary: Summary):
    obj = db.query(model).filter_by(**key).first()
    if obj is not None:
        summary.note_existing(f"{model.__name__} {key}")
        return obj, False
    obj = model(**{**key, **defaults})
    db.add(obj)
    db.flush()
    summary.note_created(f"{model.__name__} {key}")
    return obj, True


def _bh_world(db: Session, summary: Summary):
    school = db.query(School).filter(School.name == SCHOOL_NAME).first()
    if school is None:
        raise SystemExit(
            f"School {SCHOOL_NAME!r} not found; run `seed` before `exam-fixtures`."
        )
    school_class = (
        db.query(SchoolClass)
        .filter(SchoolClass.school_id == school.school_id,
                SchoolClass.name == CLASS_NAME)
        .first()
    )
    if school_class is None:
        raise SystemExit(f"Class {CLASS_NAME!r} not found in {SCHOOL_NAME}.")
    students = (
        db.query(Student)
        .filter(Student.school_id == school.school_id,
                Student.class_id == school_class.class_id,
                Student.is_active.is_(True))
        .order_by(Student.student_id)
        .all()
    )
    if not students:
        raise SystemExit(f"No active students in {CLASS_NAME}; run `seed` first.")
    subjects = {
        s.name: s
        for s in db.query(Subject).filter(Subject.school_id == school.school_id).all()
    }
    needed = [n for names in SESSION_SUBJECTS.values() for n in names]
    missing = sorted({n for n in needed if n not in subjects})
    if missing:
        raise SystemExit(f"Missing subjects in {SCHOOL_NAME}: {', '.join(missing)}")
    admin = db.query(User).filter(User.username == ADMIN_USERNAME).first()
    if admin is not None and admin.school_id not in (None, school.school_id):
        admin = None
    return school, school_class, students, subjects, admin


def _ensure_year(db: Session, school, summary: Summary) -> AcademicYear:
    year, _ = _get_or_create(
        db, AcademicYear,
        key={"school_id": school.school_id, "name": YEAR_NAME},
        defaults={"start_date": YEAR_START, "end_date": YEAR_END, "is_active": True},
        summary=summary,
    )
    return year


def _ensure_periods(db: Session, year, summary: Summary) -> dict[str, AcademicPeriod]:
    out = {}
    for name, start, end, active in (PERIOD_TERM1, PERIOD_TERM2):
        period, _ = _get_or_create(
            db, AcademicPeriod,
            key={"year_id": year.year_id, "name": name},
            defaults={"start_date": start, "end_date": end, "is_active": active},
            summary=summary,
        )
        out[name] = period
    return out


def _ensure_type(db: Session, school, summary: Summary) -> ExaminationType:
    type_, _ = _get_or_create(
        db, ExaminationType,
        key={"school_id": school.school_id, "name": EXAM_TYPE_NAME},
        defaults={"default_max_marks": EXAM_TYPE_MAX, "is_active": True},
        summary=summary,
    )
    return type_


def _ensure_scale(db: Session, school, summary: Summary) -> GradeScale:
    scale, _ = _get_or_create(
        db, GradeScale,
        key={"school_id": school.school_id, "name": SCALE_NAME, "version": 1},
        defaults={"is_active": True},
        summary=summary,
    )
    for label, rank, lo, hi in SCALE_LEVELS:
        _get_or_create(
            db, GradeLevel,
            key={"scale_id": scale.scale_id, "label": label},
            defaults={"rank": rank, "min_score": lo, "max_score": hi},
            summary=summary,
        )
    return scale


def _ensure_exam(db: Session, school, type_, year, period, name, status, scale,
                 summary: Summary) -> Examination:
    exam, _ = _get_or_create(
        db, Examination,
        key={"school_id": school.school_id, "year_id": year.year_id, "name": name},
        defaults={
            "type_id": type_.type_id,
            "period_id": period.period_id if period else None,
            "result_format_expectation": "marks",
            "grade_scale_id": scale.scale_id,
            "status": status,
        },
        summary=summary,
    )
    return exam


def _ensure_session(db: Session, exam, school_class, subject, summary: Summary):
    session_date = SESSION_DATES[(exam.name, subject.name)]
    session, _ = _get_or_create(
        db, ExaminationSession,
        key={"exam_id": exam.exam_id, "class_id": school_class.class_id,
             "subject_id": subject.subject_id, "date": session_date},
        defaults={
            "start_time": SESSION_START, "end_time": SESSION_END,
            "venue": SESSION_VENUE, "max_marks": SESSION_MAX,
            "status": "scheduled",
        },
        summary=summary,
    )
    return session


def _ensure_enrollments(db: Session, school, year, students, subjects, summary: Summary):
    admin_id = None
    admin = db.query(User).filter(User.username == ADMIN_USERNAME).first()
    if admin is not None:
        admin_id = admin.user_id
    for student in students:
        _get_or_create(
            db, ClassEnrollment,
            key={"student_id": student.student_id, "academic_year_id": year.year_id},
            defaults={"class_id": student.class_id, "status": "enrolled",
                      "decided_by": admin_id},
            summary=summary,
        )
        for subject in subjects:
            _get_or_create(
                db, StudentSubject,
                key={"student_id": student.student_id,
                     "subject_id": subject.subject_id,
                     "academic_year_id": year.year_id},
                defaults={"state": "enrolled", "is_optional": False},
                summary=summary,
            )


def _ensure_mark(db: Session, marks_repo, session, exam, student, payload,
                 summary: Summary):
    existing = (
        db.query(Mark)
        .filter(Mark.examination_session_id == session.session_id,
                Mark.student_id == student.student_id)
        .first()
    )
    if existing is not None:
        summary.note_existing(
            f"Mark session={session.session_id} student={student.student_id}"
        )
        return existing, False
    mark = marks_repo.create_session_mark(
        db, session, exam, student.student_id, session.subject_id, payload,
        user_id=None,
    )
    summary.note_created(
        f"Mark session={session.session_id} student={student.student_id} "
        f"status={mark.assessment_status}/{mark.workflow_status}"
    )
    return mark, True


def _load_marks_repo():
    from services.marks_service import repository as marks_repo
    return marks_repo


def _seed_session_marks(db: Session, marks_repo, summary: Summary, *,
                        exam_math, exam_english, exam_science, session_math,
                        session_english, session_science, students):
    by_name = {s.name: s for s in students}

    # Mathematics (Half-Yearly, draft exam): everyone except the last student
    # is submitted; the last student then gets a late exempt draft, which is
    # also the fixture behind submit's missing_student_ids report.
    late_student = students[-1]
    math_created = False
    for student in students:
        if student.student_id == late_student.student_id:
            continue
        _, created = _ensure_mark(db, marks_repo, session_math, exam_math,
                                  student, {"score": VALID_CSV_SCORE}, summary)
        math_created = math_created or created
    if math_created:
        result = marks_repo.submit_session_marks(db, session_math)
        summary.note(
            f"submit Mathematics: submitted={result['submitted']} "
            f"missing_student_ids={result['missing_student_ids']}"
        )
    _ensure_mark(db, marks_repo, session_math, exam_math, late_student,
                 {"assessment_status": LATE_DRAFT_ASSESSMENT}, summary)

    # English (Term 2 Weekly, published exam): full roster, submitted then
    # published; then one published-mark correction and one withheld mark,
    # both through the service repository (the supported workflows).
    for student in students:
        _ensure_mark(db, marks_repo, session_english, exam_english, student,
                     {"score": SCORE_ENGLISH[student.name]}, summary)
    if any(m.workflow_status == "draft"
           for m in db.query(Mark).filter(
               Mark.examination_session_id == session_english.session_id)):
        result = marks_repo.submit_session_marks(db, session_english)
        summary.note(f"submit English: submitted={result['submitted']}")
        published = marks_repo.publish_session_marks(db, session_english)
        summary.note(f"publish English: published={published['published']}")

    target = by_name["Aadhya Rao"]
    target_mark = (
        db.query(Mark).filter(
            Mark.examination_session_id == session_english.session_id,
            Mark.student_id == target.student_id)
        .one()
    )
    has_correction = (
        db.query(MarkCorrection)
        .filter(MarkCorrection.mark_id == target_mark.mark_id)
        .first() is not None
    )
    if has_correction:
        summary.note_existing(f"correction mark={target_mark.mark_id}")
    elif target_mark.score == CORRECTION_SCORE_OLD_NEW[0]:
        marks_repo.correct_mark(
            db, target_mark, {"score": CORRECTION_SCORE_OLD_NEW[1]},
            CORRECTION_REASON, user_id=None,
        )
        summary.note_created(
            f"correction mark={target_mark.mark_id} "
            f"{CORRECTION_SCORE_OLD_NEW[0]}->{CORRECTION_SCORE_OLD_NEW[1]}"
        )
    else:
        summary.note(
            f"correction skipped for mark={target_mark.mark_id} "
            f"(score={target_mark.score})"
        )

    withhold_student = by_name[WITHHOLD_STUDENT_NAME]
    withhold_mark = (
        db.query(Mark).filter(
            Mark.examination_session_id == session_english.session_id,
            Mark.student_id == withhold_student.student_id)
        .one()
    )
    if not withhold_mark.is_withheld:
        marks_repo.withhold_marks(
            db, [withhold_mark.mark_id], WITHHOLD_REASON, user_id=None)
        summary.note_created(f"withheld mark={withhold_mark.mark_id}")
    else:
        summary.note_existing(f"withheld mark={withhold_mark.mark_id}")

    # Science (Term 2 Weekly): one row per assessment status, submitted.
    science_payloads = [
        (by_name["Aadhya Rao"], {"score": SCORE_SCIENCE["Aadhya Rao"]}),
        (by_name["Anaya Reddy"], {"assessment_status": "absent"}),
        (by_name["Kiara Kapoor"], {"assessment_status": "missing"}),
    ]
    science_created = False
    for student, payload in science_payloads:
        _, created = _ensure_mark(db, marks_repo, session_science,
                                  exam_science, student, payload, summary)
        science_created = science_created or created
    if science_created:
        result = marks_repo.submit_session_marks(db, session_science)
        summary.note(f"submit Science: submitted={result['submitted']}")


def _write_csv_files(csv_dir: Path, students, summary: Summary) -> dict[str, Path]:
    csv_dir.mkdir(parents=True, exist_ok=True)
    header = ["student_id", "admission_no", "student_name",
              "score_or_grade", "assessment_status"]

    def row(student, score=VALID_CSV_SCORE, status="present"):
        return [student.student_id, student.admission_no, student.name,
                "" if score is None else score, status]

    paths = {}

    valid_path = csv_dir / "valid.csv"
    valid_lines = [",".join(header)] + [
        ",".join(str(v) for v in row(s)) for s in students
    ]
    valid_text = "\n".join(valid_lines) + "\n"
    valid_path.write_text(valid_text, encoding="utf-8")
    paths["valid"] = valid_path
    summary.note(f"csv valid.csv -> {valid_path} ({len(students)} rows)")

    # Duplicate case: byte-identical re-upload of the same session scope
    # returns the prior preview receipt (file_hash + scope_hash match).
    duplicate_path = csv_dir / "duplicate.csv"
    duplicate_path.write_text(valid_text, encoding="utf-8")
    paths["duplicate"] = duplicate_path
    summary.note(f"csv duplicate.csv -> {duplicate_path} (byte-identical)")

    # Missing-mark case: one student omitted; committing + submitting the
    # rest reports them via missing_student_ids, never inventing a zero.
    missing_students = students[:-1]
    missing_path = csv_dir / "missing-mark.csv"
    missing_path.write_text(
        "\n".join([",".join(header)] + [
            ",".join(str(v) for v in row(s)) for s in missing_students
        ]) + "\n",
        encoding="utf-8",
    )
    paths["missing-mark"] = missing_path
    summary.note(
        f"csv missing-mark.csv -> {missing_path} "
        f"({len(missing_students)} rows, omits {students[-1].name})"
    )

    # Invalid cases: out-of-range score, non-integer id, unknown student,
    # mismatched admission_no, bad assessment status, score on an absent row.
    s0 = students[0]
    invalid_rows = [
        [s0.student_id, s0.admission_no, s0.name, SESSION_MAX + 1, "present"],
        ["abc", s0.admission_no, s0.name, 50, "present"],
        [999999, "BH-9999", "Nobody", 50, "present"],
        [s0.student_id, "BH-WRONG", s0.name, 50, "present"],
        [s0.student_id, s0.admission_no, s0.name, 50, "unknown-status"],
        [s0.student_id, s0.admission_no, s0.name, 40, "absent"],
    ]
    invalid_path = csv_dir / "invalid.csv"
    invalid_path.write_text(
        "\n".join([",".join(header)] + [
            ",".join(str(v) for v in r) for r in invalid_rows
        ]) + "\n",
        encoding="utf-8",
    )
    paths["invalid"] = invalid_path
    summary.note(f"csv invalid.csv -> {invalid_path} ({len(invalid_rows)} rows)")
    return paths


def _ensure_preview_batch(db: Session, bulk, school, session, exam,
                          csv_path: Path, summary: Summary):
    content = csv_path.read_bytes()
    batch, receipt = bulk.preview_upload(
        db, school.school_id, session, exam,
        filename=csv_path.name, content=content, user_id=None,
    )
    verdicts: dict[str, int] = {}
    for row_ in receipt:
        verdicts[row_["verdict"]] = verdicts.get(row_["verdict"], 0) + 1
    summary.note(
        f"import batch {batch.batch_id} status={batch.status} "
        f"receipt={verdicts}"
    )
    return batch


def _print_invariants(db: Session, summary: Summary) -> None:
    flags = db.query(School).order_by(School.school_id).all()
    ok = True
    for school in flags:
        state = bool(school.examinations_enabled)
        mark = "ok" if not state else "FAIL"
        if state:
            ok = False
        log(f"  [{mark:^4}] examinations_enabled={state} "
            f"school={school.school_id} {school.name}")
    counts = {
        "academic_years": db.query(AcademicYear).count(),
        "academic_periods": db.query(AcademicPeriod).count(),
        "examination_types": db.query(ExaminationType).count(),
        "examinations": db.query(Examination).count(),
        "examination_sessions": db.query(ExaminationSession).count(),
        "grade_scales": db.query(GradeScale).count(),
        "grade_levels": db.query(GradeLevel).count(),
        "marks (session-scoped)": db.query(Mark).filter(
            Mark.examination_session_id.isnot(None)).count(),
        "mark_corrections": db.query(MarkCorrection).count(),
        "class_enrollments": db.query(ClassEnrollment).count(),
        "student_subjects": db.query(StudentSubject).count(),
        "import_batches": db.query(MarkImportBatch).count(),
    }
    log("fixture counts:")
    for name, count in counts.items():
        log(f"  {name:<24} {count}")
    if not ok:
        raise SystemExit(
            "examinations_enabled is on for at least one school; this seed "
            "never flips the flag and will not continue."
        )


def run(dry_run: bool = False, database_url: str | None = None,
        engine=None, csv_dir: Path | None = None) -> dict:
    """Create/refresh BH examination fixtures. Safe to re-run."""
    if engine is None:
        from seed import db as seed_db
        engine = seed_db.make_engine(database_url)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
    csv_dir = csv_dir or (ROOT / ".logs" / "exam_csv")
    summary = Summary()
    log(f"{'DRY-RUN ' if dry_run else ''}Blue Horizon examination fixtures")

    with SessionLocal() as db:
        school, school_class, students, subjects, _admin = _bh_world(db, summary)
        log(f"school_id={school.school_id} class={school_class.name} "
            f"students={[s.name for s in students]}")

        if dry_run:
            _dry_run_plan(db, school, summary)
            log("Dry run only; nothing was written.")
            return {"dry_run": True, "created": [], "existing": summary.existing,
                    "actions": summary.actions}

        year = _ensure_year(db, school, summary)
        periods = _ensure_periods(db, year, summary)
        type_ = _ensure_type(db, school, summary)
        scale = _ensure_scale(db, school, summary)

        exam_hy = _ensure_exam(db, school, type_, year, None, EXAM_HY,
                               "draft", scale, summary)
        exam_t2 = _ensure_exam(db, school, type_, year, periods["Term 2"],
                               EXAM_T2, "published", scale, summary)

        all_students = (
            db.query(Student)
            .filter(Student.school_id == school.school_id,
                    Student.is_active.is_(True))
            .order_by(Student.student_id)
            .all()
        )
        _ensure_enrollments(db, school, year, all_students,
                            list(subjects.values()), summary)

        session_math = _ensure_session(db, exam_hy, school_class,
                                       subjects["Mathematics"], summary)
        session_english = _ensure_session(db, exam_t2, school_class,
                                          subjects["English"], summary)
        session_science = _ensure_session(db, exam_t2, school_class,
                                          subjects["Science"], summary)
        session_hindi = _ensure_session(db, exam_hy, school_class,
                                        subjects["Hindi"], summary)

        marks_repo = _load_marks_repo()
        _seed_session_marks(
            db, marks_repo, summary,
            exam_math=exam_hy, exam_english=exam_t2, exam_science=exam_t2,
            session_math=session_math, session_english=session_english,
            session_science=session_science, students=students,
        )

        paths = _write_csv_files(csv_dir, students, summary)
        bulk = _load_bulk()
        _ensure_preview_batch(db, bulk, school, session_hindi, exam_hy,
                              paths["valid"], summary)

        db.commit()
        _print_invariants(db, summary)

    log("done")
    return {
        "dry_run": False,
        "created": summary.created,
        "existing": summary.existing,
        "actions": summary.actions,
    }


def _dry_run_plan(db: Session, school, summary: Summary) -> None:
    year = db.query(AcademicYear).filter(
        AcademicYear.school_id == school.school_id,
        AcademicYear.name == YEAR_NAME).first()
    log(f"would ensure: academic year {YEAR_NAME} "
        f"({'exists' if year else 'MISSING'})")
    for model, label in [(AcademicPeriod, "periods"), (ExaminationType, "types"),
                         (Examination, "examinations"),
                         (ExaminationSession, "sessions"),
                         (GradeScale, "grade scales"),
                         (Mark, "marks (table total)"),
                         (ClassEnrollment, "enrollments"),
                         (StudentSubject, "student subjects"),
                         (MarkImportBatch, "import batches")]:
        count = db.query(model).count()
        log(f"would ensure: {label} (currently {count} rows in table)")
