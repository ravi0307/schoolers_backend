"""
Functional tests for the CSV bulk import/export pipeline (Phase 4).

Covers template generation, preview validation, atomic commit, admin-only
partial application, idempotent retry, closed-session refusal, and
formula-guarded exports. Uses the real CSV text end to end.
"""
import sys
import unittest
from datetime import date, time

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.dependencies import CurrentUser
from common.exceptions import AppError, ConflictError
from common.models import (
    Base, AcademicYear, Examination, ExaminationSession, ExaminationType,
    GradeLevel, GradeScale, Mark, Student, Subject, TeacherClassSubject,
)
import services.marks_service.repository as repo

TABLES = [
    "academic_years", "examination_types", "examinations", "examination_sessions",
    "grade_scales", "grade_levels", "students", "subjects",
    "teacher_class_subjects", "marks", "mark_import_batches", "mark_import_rows",
]


def _load_bulk():
    sys.path.insert(0, "services/marks_service")
    try:
        for mod in ("repository", "schemas", "bulk"):
            sys.modules.pop(mod, None)
        from services.marks_service import bulk as bulk_mod
        return bulk_mod
    finally:
        sys.path.pop(0)
        for mod in ("repository", "schemas", "bulk"):
            sys.modules.pop(mod, None)


def _csv(rows):
    header = "student_id,admission_no,student_name,score_or_grade,assessment_status"
    return "\n".join([header] + rows) + "\n"


class MarksImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            cls.engine,
            tables=[Base.metadata.tables[name] for name in TABLES],
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

    def setUp(self):
        self.db: Session = self.Session()
        for name in TABLES:
            self.db.execute(Base.metadata.tables[name].delete())
        self.db.commit()
        self.db.add_all([
            AcademicYear(year_id=1, school_id=1, name="2026-27",
                         start_date=date(2026, 6, 1), end_date=date(2027, 5, 31)),
            ExaminationType(type_id=1, school_id=1, name="Half-Yearly", default_max_marks=100),
            Examination(exam_id=1, school_id=1, type_id=1, year_id=1,
                        name="Half-Yearly", result_format_expectation="marks", status="draft"),
            ExaminationSession(session_id=1, exam_id=1, class_id=1, subject_id=3,
                               date=date(2026, 12, 15), start_time=time(9, 0),
                               end_time=time(12, 0), max_marks=100, status="scheduled"),
            Student(student_id=1, school_id=1, class_id=1, admission_no="A1", name="Riya"),
            Student(student_id=2, school_id=1, class_id=1, admission_no="A2", name="Dev"),
            Student(student_id=3, school_id=1, class_id=2, admission_no="A3", name="Una"),
            Subject(subject_id=3, school_id=1, name="Maths"),
            TeacherClassSubject(staff_id=7, class_id=1, subject_id=3),
        ])
        self.db.commit()
        self.session1 = self.db.get(ExaminationSession, 1)
        self.exam1 = self.db.get(Examination, 1)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _preview(self, text, filename="marks.csv"):
        return BULK.preview_upload(
            self.db, 1, self.session1, self.exam1, filename, text.encode(), 31)

    def test_template_lists_active_class_students(self):
        text = BULK.render_template(self.db, self.session1)
        lines = text.strip().splitlines()
        self.assertEqual(lines[0], ",".join(BULK.TEMPLATE_COLUMNS))
        self.assertEqual(len(lines), 3)  # header + Riya + Dev (Una is class 2)

    def test_preview_accepts_valid_rows(self):
        batch, receipt = self._preview(_csv(["1,A1,Riya,80,present", "2,A2,Dev,90,present"]))
        self.assertEqual(batch.status, "preview")
        self.assertEqual([r["verdict"] for r in receipt], ["valid", "valid"])
        # Nothing written to marks at preview.
        self.assertEqual(self.db.query(Mark).count(), 0)

    def test_preview_rejects_bad_rows_with_reasons(self):
        batch, receipt = self._preview(_csv([
            "1,A1,Riya,80,present",
            "2,A2,Dev,90,present",
            "9,B9,Ghost,70,present",      # not in session class
            "2,WRONG,Dev,70,present",     # admission mismatch
            "2,A2,Dev,101,present",       # over session max
            "2,A2,Dev,,dancing",          # unknown status
            "2,A2,Dev,50,absent",         # value on absent row
            "2,A2,Dev,70,present",        # duplicate in file
        ]))
        verdicts = [r["verdict"] for r in receipt]
        self.assertEqual(verdicts[:2], ["valid", "valid"])
        self.assertEqual(verdicts[2:], ["invalid"] * 6)
        self.assertTrue(all(r["errors"] for r in receipt[2:]))

    def test_clash_with_submitted_mark_is_invalid(self):
        repo.create_session_mark(
            self.db, self.session1, self.exam1, 1, 3,
            {"assessment_status": "present", "score": 80}, 31)
        repo.submit_session_marks(self.db, self.session1)
        _, receipt = self._preview(_csv(["1,A1,Riya,85,present", "2,A2,Dev,90,present"]))
        self.assertEqual(receipt[0]["verdict"], "invalid")
        self.assertIn("submitted or published", receipt[0]["errors"][0])
        self.assertEqual(receipt[1]["verdict"], "valid")

    def test_commit_is_atomic_by_default(self):
        batch, _ = self._preview(_csv(["1,A1,Riya,80,present", "9,B9,Ghost,70,present"]))
        with self.assertRaises(ConflictError):
            BULK.commit_batch(self.db, batch, apply_partial=False, is_admin=False)
        self.assertEqual(self.db.query(Mark).count(), 0)
        self.assertEqual(batch.status, "preview")

    def test_partial_commit_is_admin_only(self):
        batch, _ = self._preview(_csv(["1,A1,Riya,80,present", "9,B9,Ghost,70,present"]))
        with self.assertRaises(AppError) as ctx:
            BULK.commit_batch(self.db, batch, apply_partial=True, is_admin=False)
        self.assertEqual(ctx.exception.status_code, 403)
        out = BULK.commit_batch(self.db, batch, apply_partial=True, is_admin=True)
        self.assertEqual((out["applied"], out["skipped"]), (1, 1))

    def test_commit_applies_and_updates_drafts(self):
        repo.create_session_mark(
            self.db, self.session1, self.exam1, 1, 3,
            {"assessment_status": "present", "score": 70}, 31)
        batch, _ = self._preview(_csv(["1,A1,Riya,80,present", "2,A2,Dev,90,present"]))
        out = BULK.commit_batch(self.db, batch, apply_partial=False, is_admin=False)
        self.assertEqual((out["applied"], out["updated_drafts"], out["skipped"]), (1, 1, 0))
        self.assertEqual(
            self.db.query(Mark).filter(Mark.student_id == 1).one().score, 80)

    def test_idempotent_retry_returns_prior_receipt(self):
        text = _csv(["1,A1,Riya,80,present"])
        batch1, _ = self._preview(text)
        out = BULK.commit_batch(self.db, batch1, apply_partial=False, is_admin=False)
        self.assertFalse(out["duplicate_receipt"])
        batch2, receipt2 = self._preview(text)
        self.assertEqual(batch1.batch_id, batch2.batch_id)
        out2 = BULK.commit_batch(self.db, batch2, apply_partial=False, is_admin=False)
        self.assertTrue(out2["duplicate_receipt"])
        self.assertEqual(self.db.query(Mark).count(), 1)

    def test_closed_session_refuses_preview(self):
        self.session1.status = "cancelled"
        self.db.commit()
        with self.assertRaises(ConflictError):
            self._preview(_csv(["1,A1,Riya,80,present"]))

    def test_export_guards_formulas(self):
        evil = Student(student_id=4, school_id=1, class_id=1, admission_no="A4", name="=2+2")
        self.db.add(evil)
        self.db.commit()
        repo.create_session_mark(
            self.db, self.session1, self.exam1, 4, 3,
            {"assessment_status": "present", "score": 60}, 31)
        rows = [m for m in repo.get_for_class(self.db, 1)
                if m.examination_session_id == 1]
        text = BULK.export_session_csv(self.db, self.session1, rows)
        self.assertIn("'=2+2", text)
        self.assertNotIn("\n=2+2", text)


BULK = _load_bulk()


if __name__ == "__main__":
    unittest.main()
