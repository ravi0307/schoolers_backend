"""
Tests for the idempotent Blue Horizon examination fixture seeder
(seed/exams.py): state coverage, supported-workflow mark moves, CSV fixture
files, re-run idempotency, dry-run safety, and the hard rules (no password
rewrites, no legacy-mark damage, no flag flips).
"""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.models import (
    Base, AcademicPeriod, AcademicYear, ClassEnrollment, Examination,
    ExaminationSession, ExaminationType, GradeLevel, GradeScale, Mark,
    MarkCorrection, MarkImportBatch, MarkImportRow, School, SchoolClass,
    Student, StudentSubject, Subject, User,
)
from seed import exams as exams_mod

TABLES = [
    "schools", "users", "classes", "subjects", "students",
    "academic_years", "academic_periods", "examination_types", "examinations",
    "examination_sessions", "grade_scales", "grade_levels", "marks",
    "mark_corrections", "class_enrollments", "student_subjects",
    "mark_import_batches", "mark_import_rows",
]

SENTINEL_HASH = "unchanged-password-hash"


def _school(school_id, name):
    return School(
        school_id=school_id, name=name, address="1 MG Road",
        pincode="560001", city="Bengaluru", state="Karnataka",
        primary_contact="8000000000", primary_email="dev@example.com",
        examinations_enabled=False,
    )


class ExamFixtureSeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            cls.engine,
            tables=[Base.metadata.tables[name] for name in TABLES],
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

    def setUp(self):
        db = self.Session()
        for name in TABLES:
            db.execute(Base.metadata.tables[name].delete())
        db.add_all([
            _school(1, "Blue Horizon Academy"),
            _school(2, "Green Valley Public School"),
            User(user_id=1, school_id=1, role="admin",
                 username="blue.horizon", password_hash=SENTINEL_HASH),
            SchoolClass(class_id=1, school_id=1, name="Grade I",
                        student_count=3),
            Subject(subject_id=1, school_id=1, name="Mathematics"),
            Subject(subject_id=2, school_id=1, name="English"),
            Subject(subject_id=3, school_id=1, name="Science"),
            Subject(subject_id=4, school_id=1, name="Hindi"),
            Student(student_id=1, school_id=1, class_id=1,
                    admission_no="BH-0001", name="Aadhya Rao"),
            Student(student_id=7, school_id=1, class_id=1,
                    admission_no="BH-0007", name="Anaya Reddy"),
            Student(student_id=13, school_id=1, class_id=1,
                    admission_no="BH-0013", name="Kiara Kapoor"),
            # Legacy (session-less) demo mark: must survive untouched.
            Mark(mark_id=1, student_id=1, subject_id=1, term="Term 1",
                 score=42, max_marks=100),
        ])
        db.commit()
        db.close()
        self._tmp = tempfile.TemporaryDirectory()
        self.csv_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, dry_run=False):
        return exams_mod.run(dry_run=dry_run, engine=self.engine,
                             csv_dir=self.csv_dir)

    def _counts(self):
        with self.Session() as db:
            return {
                "years": db.query(AcademicYear).count(),
                "periods": db.query(AcademicPeriod).count(),
                "types": db.query(ExaminationType).count(),
                "exams": db.query(Examination).count(),
                "sessions": db.query(ExaminationSession).count(),
                "scales": db.query(GradeScale).count(),
                "levels": db.query(GradeLevel).count(),
                "session_marks": db.query(Mark).filter(
                    Mark.examination_session_id.isnot(None)).count(),
                "corrections": db.query(MarkCorrection).count(),
                "enrollments": db.query(ClassEnrollment).count(),
                "student_subjects": db.query(StudentSubject).count(),
                "batches": db.query(MarkImportBatch).count(),
                "batch_rows": db.query(MarkImportRow).count(),
                "users": db.query(User).count(),
                "total_marks": db.query(Mark).count(),
            }

    def test_dry_run_writes_nothing(self):
        self._run(dry_run=True)
        counts = self._counts()
        self.assertEqual(counts["years"], 0)
        self.assertEqual(counts["exams"], 0)
        self.assertEqual(counts["session_marks"], 0)
        self.assertEqual(counts["total_marks"], 1)  # legacy only

    def test_first_run_creates_full_fixture_state(self):
        self._run()
        with self.Session() as db:
            year = db.query(AcademicYear).filter_by(name="2026-27").one()
            self.assertEqual(year.start_date, date(2026, 6, 1))
            self.assertEqual(year.end_date, date(2027, 5, 31))
            periods = {p.name: p for p in db.query(AcademicPeriod).all()}
            self.assertEqual(set(periods), {"Term 1", "Term 2"})
            self.assertFalse(periods["Term 1"].is_active)
            self.assertTrue(periods["Term 2"].is_active)

            scale = db.query(GradeScale).one()
            levels = {l.label: l for l in db.query(GradeLevel).all()}
            self.assertEqual(levels["A"].min_score, 80)
            self.assertEqual(levels["A"].max_score, 100)
            self.assertEqual(levels["B"].min_score, 60)
            self.assertEqual(levels["B"].max_score, 79)

            hy = db.query(Examination).filter_by(name="Half-Yearly").one()
            t2 = db.query(Examination).filter_by(name="Term 2 Weekly").one()
            self.assertEqual(hy.status, "draft")
            self.assertIsNone(hy.period_id)
            self.assertEqual(t2.status, "published")
            self.assertEqual(t2.period_id, periods["Term 2"].period_id)
            self.assertEqual(hy.grade_scale_id, scale.scale_id)

            math = db.query(ExaminationSession).filter_by(
                subject_id=1, exam_id=hy.exam_id).one()
            english = db.query(ExaminationSession).filter_by(
                subject_id=2, exam_id=t2.exam_id).one()
            science = db.query(ExaminationSession).filter_by(
                subject_id=3, exam_id=t2.exam_id).one()
            hindi = db.query(ExaminationSession).filter_by(
                subject_id=4, exam_id=hy.exam_id).one()
            self.assertEqual(math.date, date(2026, 12, 15))
            self.assertEqual(english.date, date(2026, 12, 16))
            self.assertEqual(science.date, date(2026, 12, 17))
            self.assertEqual(hindi.date, date(2026, 12, 18))

            def marks_of(session_id):
                return {
                    m.student_id: m
                    for m in db.query(Mark).filter_by(
                        examination_session_id=session_id).all()
                }

            math_marks = marks_of(math.session_id)
            self.assertEqual(math_marks[1].workflow_status, "submitted")
            self.assertEqual(math_marks[7].workflow_status, "submitted")
            self.assertEqual(math_marks[13].workflow_status, "draft")
            self.assertEqual(math_marks[13].assessment_status, "exempt")
            self.assertIsNone(math_marks[13].score)

            english_marks = marks_of(english.session_id)
            for m in english_marks.values():
                self.assertEqual(m.workflow_status, "published")
            self.assertEqual(english_marks[1].score, 72)  # corrected 67 -> 72
            self.assertTrue(english_marks[7].is_withheld)
            self.assertEqual(english_marks[7].withheld_reason,
                             "Pending verification documents")

            correction = db.query(MarkCorrection).one()
            self.assertEqual(correction.old_score, 67)
            self.assertEqual(correction.new_score, 72)
            self.assertEqual(correction.reason,
                             "Answer script re-evaluated during verification")

            science_marks = marks_of(science.session_id)
            statuses = {m.student_id: m.assessment_status
                        for m in science_marks.values()}
            self.assertEqual(statuses,
                             {1: "present", 7: "absent", 13: "missing"})
            self.assertIsNone(science_marks[7].score)
            self.assertIsNone(science_marks[13].score)
            for m in science_marks.values():
                self.assertEqual(m.workflow_status, "submitted")

            self.assertEqual(db.query(ClassEnrollment).count(), 3)
            self.assertEqual(db.query(StudentSubject).count(), 12)

            batch = db.query(MarkImportBatch).one()
            self.assertEqual(batch.status, "preview")
            rows = db.query(MarkImportRow).order_by(
                MarkImportRow.row_no).all()
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(r.verdict == "valid" for r in rows))

            # CSV fixture files
            valid = (self.csv_dir / "valid.csv").read_text()
            self.assertTrue(valid.startswith(
                "student_id,admission_no,student_name,score_or_grade,"
                "assessment_status\n"))
            self.assertEqual(len(valid.strip().splitlines()), 4)
            self.assertEqual((self.csv_dir / "duplicate.csv").read_bytes(),
                             (self.csv_dir / "valid.csv").read_bytes())
            missing = (self.csv_dir / "missing-mark.csv").read_text()
            self.assertEqual(len(missing.strip().splitlines()), 3)
            invalid = (self.csv_dir / "invalid.csv").read_text()
            self.assertEqual(len(invalid.strip().splitlines()), 7)

            # Hard rules
            legacy = db.query(Mark).filter_by(mark_id=1).one()
            self.assertEqual(legacy.score, 42)
            self.assertIsNone(legacy.examination_session_id)
            admin = db.query(User).one()
            self.assertEqual(admin.password_hash, SENTINEL_HASH)
            for school in db.query(School).all():
                self.assertFalse(school.examinations_enabled)

    def test_second_run_is_idempotent(self):
        self._run()
        before = self._counts()
        result = self._run()
        after = self._counts()
        self.assertEqual(before, after)
        self.assertEqual(after["corrections"], 1)
        self.assertEqual(after["batches"], 1)
        self.assertEqual(after["periods"], 2)
        self.assertEqual(after["total_marks"], 10)  # 9 session + 1 legacy
        self.assertTrue(any("Dry" not in a for a in result["actions"]))
        with self.Session() as db:
            english = db.query(ExaminationSession).filter_by(
                subject_id=2).one()
            aadhya = db.query(Mark).filter_by(
                examination_session_id=english.session_id,
                student_id=1).one()
            self.assertEqual(aadhya.score, 72)
            self.assertEqual(
                db.query(MarkCorrection).count(), 1)


if __name__ == "__main__":
    unittest.main()
