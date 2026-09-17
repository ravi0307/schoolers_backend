"""
Functional tests for teacher marks (post/edit) flows.

Runs against an in-memory SQLite database. Exercises the ON CONFLICT
upsert target (student_id, subject_id, term), the teacher assignment
guardrails (you may only grade subjects you teach in the student's own
class), and the class-scoped read used to preload the marks grid.
"""
import unittest

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from common.models import Base, Mark, Student, Subject, TeacherClassSubject
import services.marks_service.repository as repo

TABLES = ["marks", "students", "subjects", "teacher_class_subjects"]


def seed(db: Session):
    db.add_all(
        [
            Student(student_id=1, school_id=1, class_id=1, admission_no="A1", name="Alice"),
            Student(student_id=2, school_id=1, class_id=1, admission_no="A2", name="Bob"),
            Student(student_id=3, school_id=1, class_id=2, admission_no="A3", name="Carol"),
            Subject(subject_id=3, name="Maths"),
            Subject(subject_id=5, name="Science"),
            Subject(subject_id=8, name="English"),
            TeacherClassSubject(teacher_id=7, class_id=1, subject_id=3, is_class_teacher=True),
            TeacherClassSubject(teacher_id=7, class_id=1, subject_id=8, is_class_teacher=True),
            TeacherClassSubject(teacher_id=9, class_id=2, subject_id=3, is_class_teacher=True),
        ]
    )
    db.commit()


class MarksEditingTests(unittest.TestCase):
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
        seed(self.db)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_schema_declares_unique_constraint_for_upsert_target(self):
        """The upsert target (student_id, subject_id, term) must be unique."""
        inspector = inspect(self.engine)
        uniques = inspector.get_unique_constraints("marks")
        names = {tuple(sorted(u["column_names"])) for u in uniques}
        self.assertIn(("student_id", "subject_id", "term"), names)

    def test_upsert_creates_mark(self):
        mark = repo.upsert_mark(self.db, student_id=1, subject_id=3, term="Term 1", score=88, updated_by=7)
        self.assertEqual(mark.student_id, 1)
        self.assertEqual(mark.subject_id, 3)
        self.assertEqual(mark.term, "Term 1")
        self.assertEqual(mark.score, 88)
        self.assertEqual(mark.updated_by, 7)

        count = self.db.query(Mark).filter(Mark.student_id == 1, Mark.subject_id == 3).count()
        self.assertEqual(count, 1)

    def test_upsert_same_term_overwrites_without_duplicate(self):
        repo.upsert_mark(self.db, student_id=1, subject_id=3, term="Term 1", score=60, updated_by=7)
        mark = repo.upsert_mark(self.db, student_id=1, subject_id=3, term="Term 1", score=95, updated_by=8)
        self.assertEqual(mark.score, 95)
        self.assertEqual(mark.updated_by, 8)

        marks = self.db.query(Mark).filter(Mark.student_id == 1, Mark.subject_id == 3).all()
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0].score, 95)

    def test_upsert_keeps_terms_independent(self):
        repo.upsert_mark(self.db, student_id=1, subject_id=3, term="Term 1", score=50, updated_by=7)
        repo.upsert_mark(self.db, student_id=1, subject_id=3, term="Term 2", score=80, updated_by=7)
        marks = self.db.query(Mark).filter(Mark.student_id == 1, Mark.subject_id == 3).all()
        by_term = {m.term: m.score for m in marks}
        self.assertEqual(by_term, {"Term 1": 50, "Term 2": 80})

    def test_teacher_can_grade_allows_assigned_subject(self):
        self.assertTrue(repo.teacher_can_grade(self.db, teacher_id=7, student_id=1, subject_id=3))
        self.assertTrue(repo.teacher_can_grade(self.db, teacher_id=7, student_id=2, subject_id=8))

    def test_teacher_can_grade_rejects_unassigned_subject(self):
        self.assertFalse(repo.teacher_can_grade(self.db, teacher_id=7, student_id=1, subject_id=5))

    def test_teacher_can_grade_rejects_student_in_untaught_class(self):
        # Carol is in class 2, which teacher 7 doesn't teach.
        self.assertFalse(repo.teacher_can_grade(self.db, teacher_id=7, student_id=3, subject_id=3))

    def test_get_subject_ids_for_class_returns_assigned_subjects(self):
        subject_ids = repo.get_subject_ids_for_class(self.db, teacher_id=7, class_id=1)
        self.assertEqual(sorted(subject_ids), [3, 8])
        self.assertEqual(repo.get_subject_ids_for_class(self.db, teacher_id=7, class_id=2), [])

    def test_get_for_class_filters_to_subjects_and_class(self):
        repo.upsert_mark(self.db, student_id=1, subject_id=3, term="Term 1", score=70, updated_by=7)
        repo.upsert_mark(self.db, student_id=1, subject_id=5, term="Term 1", score=60, updated_by=7)
        repo.upsert_mark(self.db, student_id=2, subject_id=3, term="Term 1", score=80, updated_by=7)
        # Carol (class 2) shares subject 3 but must not leak into class 1.
        repo.upsert_mark(self.db, student_id=3, subject_id=3, term="Term 1", score=90, updated_by=9)

        rows = repo.get_for_class(self.db, class_id=1, subject_ids=[3])
        got = {(r.student_id, r.subject_id) for r in rows}
        self.assertEqual(got, {(1, 3), (2, 3)})
        self.assertEqual(all(r.score in (70, 80) for r in rows), True)

    def test_get_for_class_without_filter_returns_all_subjects_of_class(self):
        repo.upsert_mark(self.db, student_id=1, subject_id=3, term="Term 1", score=70, updated_by=7)
        repo.upsert_mark(self.db, student_id=1, subject_id=5, term="Term 1", score=60, updated_by=7)
        rows = repo.get_for_class(self.db, class_id=1)
        got = {(r.student_id, r.subject_id) for r in rows}
        self.assertEqual(got, {(1, 3), (1, 5)})

    def test_upsert_records_admin_audit_as_user_id_only(self):
        # Admins have no teacher row, so updated_by stays None while the
        # acting user id is still recorded for the audit trail.
        mark = repo.upsert_mark(
            self.db, student_id=1, subject_id=3, term="Term 1", score=75,
            updated_by=None, updated_by_user=4,
        )
        self.assertIsNone(mark.updated_by)
        self.assertEqual(mark.updated_by_user, 4)

    def test_upsert_overwrite_refreshes_audit_columns(self):
        repo.upsert_mark(
            self.db, student_id=1, subject_id=3, term="Term 1", score=60,
            updated_by=7, updated_by_user=2,
        )
        mark = repo.upsert_mark(
            self.db, student_id=1, subject_id=3, term="Term 1", score=90,
            updated_by=None, updated_by_user=4,
        )
        self.assertEqual(mark.score, 90)
        self.assertIsNone(mark.updated_by)
        self.assertEqual(mark.updated_by_user, 4)

    def test_student_exists_distinguishes_known_from_unknown(self):
        self.assertTrue(repo.student_exists(self.db, 1))
        self.assertFalse(repo.student_exists(self.db, 4242))

    def test_subject_exists_distinguishes_known_from_unknown(self):
        self.assertTrue(repo.subject_exists(self.db, 3))
        self.assertFalse(repo.subject_exists(self.db, 999))

    def test_student_in_school_scopes_by_school(self):
        self.assertTrue(repo.student_in_school(self.db, 1, 1))
        self.assertFalse(repo.student_in_school(self.db, 1, 2))
        self.assertFalse(repo.student_in_school(self.db, 4242, 1))

    def test_teacher_teaches_student_checks_students_class(self):
        # Teacher 7 teaches class 1 -> may read Alice and Bob, not Carol (class 2).
        self.assertTrue(repo.teacher_teaches_student(self.db, teacher_id=7, student_id=1))
        self.assertTrue(repo.teacher_teaches_student(self.db, teacher_id=7, student_id=2))
        self.assertFalse(repo.teacher_teaches_student(self.db, teacher_id=7, student_id=3))
        self.assertFalse(repo.teacher_teaches_student(self.db, teacher_id=7, student_id=4242))


if __name__ == "__main__":
    unittest.main()