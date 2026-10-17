"""
Phase 7 hardening: DDL compiles on both dialects, DB-level backstops hold,
and a fresh (flag-off) install behaves exactly like the legacy system.

A full-metadata SQLite build is impossible for a pre-existing reason
(website_builder_sites uses PG-only JSONB), so new/altered tables are
compiled per-dialect instead of created wholesale.
"""
import unittest

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql, sqlite

from common.models import Base
import services.marks_service.repository as repo

NEW_TABLES = [
    "academic_years", "academic_periods", "academic_events",
    "examination_types", "grade_scales", "grade_levels",
    "examinations", "examination_sessions", "examination_session_versions",
    "class_enrollments", "promotion_batches", "class_subjects", "student_subjects",
    "mark_corrections", "mark_import_batches", "mark_import_rows",
    "marks", "schools",
]


class DdlCompilesBothDialectsTests(unittest.TestCase):
    def test_new_tables_render_on_postgres_and_sqlite(self):
        pg, lite = postgresql.dialect(), sqlite.dialect()
        for name in NEW_TABLES:
            table = Base.metadata.tables[name]
            pg_sql = str(CreateTable(table).compile(dialect=pg))
            lite_sql = str(CreateTable(table).compile(dialect=lite))
            self.assertIn("CREATE TABLE", pg_sql)
            self.assertIn("CREATE TABLE", lite_sql)

    def test_partial_uniques_render_where_clauses(self):
        from sqlalchemy.schema import CreateIndex
        from common.models import Mark

        ddl = str(CreateTable(Mark.__table__).compile(
            dialect=postgresql.dialect()))
        for index in Mark.__table__.indexes:
            ddl += str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        self.assertIn("marks_legacy_unique", ddl)
        self.assertIn("marks_session_unique", ddl)
        self.assertIn("examination_session_id IS NULL", ddl)
        self.assertIn("examination_session_id IS NOT NULL", ddl)
        self.assertIn("marks_payload_coherence", ddl)


class DbBackstopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            cls.engine,
            tables=[Base.metadata.tables[n] for n in ("students", "subjects", "marks")],
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

    def setUp(self):
        self.db: Session = self.Session()
        for name in ("students", "subjects", "marks"):
            self.db.execute(Base.metadata.tables[name].delete())
        self.db.commit()
        from common.models import Student, Subject
        self.db.add_all([
            Student(student_id=1, school_id=1, class_id=1, admission_no="A1", name="R"),
            Subject(subject_id=3, school_id=1, name="M"),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _row(self, **over):
        from common.models import Mark
        base = dict(student_id=1, subject_id=3, term="Term 1", score=80,
                    max_marks=100, result_format="marks", workflow_status="published",
                    assessment_status="present", is_withheld=False)
        base.update(over)
        self.db.add(Mark(**base))
        self.db.commit()

    def test_absent_with_score_rejected_by_database(self):
        from sqlalchemy.exc import IntegrityError
        with self.assertRaises(IntegrityError):
            self._row(assessment_status="absent")

    def test_present_marks_without_max_rejected_by_database(self):
        from sqlalchemy.exc import IntegrityError
        with self.assertRaises(IntegrityError):
            self._row(max_marks=None)

    def test_grade_row_with_score_rejected_by_database(self):
        from sqlalchemy.exc import IntegrityError
        with self.assertRaises(IntegrityError):
            self._row(result_format="grade", grade_level_id=1)

    def test_duplicate_legacy_row_rejected_by_database(self):
        from sqlalchemy.exc import IntegrityError
        self._row()
        with self.assertRaises(IntegrityError):
            self._row()


class FreshInstallParityTests(unittest.TestCase):
    """Flag-off behavior: with no years/exams/sessions anywhere, the system
    behaves exactly like the legacy one (list reads + upsert + legacy terms)."""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            cls.engine,
            tables=[Base.metadata.tables[n] for n in
                    ("students", "subjects", "marks", "teacher_class_subjects")],
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

    def setUp(self):
        self.db: Session = self.Session()
        for name in ("students", "subjects", "marks", "teacher_class_subjects"):
            self.db.execute(Base.metadata.tables[name].delete())
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_legacy_only_results_shape(self):
        from common.models import Student, Subject
        self.db.add_all([
            Student(student_id=1, school_id=1, class_id=1, admission_no="A1", name="R"),
            Subject(subject_id=3, school_id=1, name="Maths"),
        ])
        self.db.commit()
        repo.upsert_mark(self.db, 1, 3, "Term 1", 70, 7)
        out = repo.student_results(self.db, 1, 1)
        self.assertEqual(out["examinations"], [])
        self.assertEqual(len(out["legacy_terms"]), 1)
        self.assertEqual(out["legacy_terms"][0]["obtained"], 70)
        # Year scopes see only session rows: legacy never double-counts.
        self.assertIsNone(repo.student_results(self.db, 1, 1, year_id=1)["aggregate"])
        self.assertIsNone(out["aggregate"])


if __name__ == "__main__":
    unittest.main()
