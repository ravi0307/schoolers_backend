"""
Functional tests for examination scheduling (Phase 3): examinations,
sessions, transactional conflicts, amendments with version history.

Covers school-scoped CRUD, publish rules, blocking vs warning conflicts,
reschedule versioning, cancellation preserving rows, and cross-school 404s.
"""
import sys
import unittest
from datetime import date, time

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.dependencies import CurrentUser
from common.exceptions import AppError, ConflictError, NotFoundError
from common.models import (
    Base, AcademicYear, AcademicPeriod, ExaminationType, Examination,
    ExaminationSession, ExaminationSessionVersion, GradeScale,
    Holiday, SchoolClass, Subject,
)
import services.academics_service.repository as repo

TABLES = [
    "academic_years", "academic_periods", "examination_types", "examinations",
    "examination_sessions", "examination_session_versions",
    "classes", "subjects", "holidays", "academic_events", "grade_scales",
    "class_subjects", "student_subjects",
]


def _load_academics():
    sys.path.insert(0, "services/academics_service")
    try:
        for mod in ("repository", "schemas"):
            sys.modules.pop(mod, None)
        from services.academics_service import router as router_mod
        from services.academics_service import schemas as schemas_mod
        return router_mod, schemas_mod
    finally:
        sys.path.pop(0)
        for mod in ("repository", "schemas"):
            sys.modules.pop(mod, None)


def _exam_data(**over):
    data = {
        "type_id": 1, "year_id": 1, "period_id": 1, "name": "Half-Yearly",
        "description": None, "result_format_expectation": "marks", "grade_scale_id": None,
    }
    data.update(over)
    return data


def _session_data(**over):
    data = {
        "class_id": 1, "subject_id": 3, "date": date(2026, 12, 15),
        "start_time": time(9, 0), "end_time": time(12, 0),
        "venue": "Hall A", "max_marks": 100,
    }
    data.update(over)
    return data


class ExaminationSchedulingTests(unittest.TestCase):
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
            AcademicPeriod(period_id=1, year_id=1, name="Term 1",
                           start_date=date(2026, 6, 1), end_date=date(2026, 12, 31)),
            ExaminationType(type_id=1, school_id=1, name="Half-Yearly", default_max_marks=100),
            ExaminationType(type_id=2, school_id=2, name="Half-Yearly", default_max_marks=100),
            SchoolClass(class_id=1, school_id=1, name="8-A"),
            SchoolClass(class_id=2, school_id=1, name="8-B"),
            SchoolClass(class_id=9, school_id=2, name="8-A"),
            Subject(subject_id=3, school_id=1, name="Maths"),
            Subject(subject_id=5, school_id=1, name="Science"),
            Subject(subject_id=7, school_id=2, name="Maths"),
            Holiday(school_id=1, occasion="Diwali", holiday_date=date(2026, 11, 5)),
        ])
        self.db.commit()
        self.admin1 = CurrentUser(user_id=11, role="admin", school_id=1, linked_person_id=None)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _exam(self, **over):
        return repo.create_examination(self.db, 1, 11, _exam_data(**over))

    def _publish_with_session(self, exam=None, **sover):
        exam = exam or self._exam()
        session, _ = repo.create_session(self.db, exam, 11, _session_data(**sover))
        return repo.publish_examination(self.db, exam), session

    def test_create_validates_school_membership(self):
        with self.assertRaises(NotFoundError):
            repo.create_examination(self.db, 1, 11, _exam_data(type_id=2))
        with self.assertRaises(NotFoundError):
            repo.create_examination(self.db, 1, 11, _exam_data(period_id=4242))

    def test_exam_name_unique_per_school_year(self):
        self._exam()
        with self.assertRaises(ConflictError):
            self._exam()

    def test_publish_requires_a_scheduled_session(self):
        exam = self._exam()
        with self.assertRaises(ConflictError):
            repo.publish_examination(self.db, exam)
        self._publish_with_session(exam)
        self.assertEqual(
            self.db.get(Examination, exam.exam_id).status, "published"
        )

    def test_draft_only_edits_and_delete_rules(self):
        exam, _ = self._publish_with_session()
        with self.assertRaises(ConflictError):
            repo.update_examination_draft(self.db, exam, {"name": "X"})
        router, _ = _load_academics()
        with self.assertRaises(ConflictError):
            router.delete_examination(exam.exam_id, self.db, 1, self.admin1)
        draft = self._exam(name="Unit 1")
        router.delete_examination(draft.exam_id, self.db, 1, self.admin1)
        self.assertIsNone(repo.get_examination(self.db, 1, draft.exam_id))

    def test_same_class_overlap_with_published_is_blocking(self):
        self._publish_with_session()
        exam_b = self._exam(name="Unit 1")
        with self.assertRaises(ConflictError) as ctx:
            repo.create_session(self.db, exam_b, 11, _session_data(
                date=date(2026, 12, 15), start_time=time(10, 0), end_time=time(13, 0)))
        self.assertIn("already scheduled", str(ctx.exception))

    def test_draft_vs_draft_overlap_is_warning_not_blocking(self):
        exam_a = self._exam(name="A")
        repo.create_session(self.db, exam_a, 11, _session_data())
        exam_b = self._exam(name="B")
        session, warnings = repo.create_session(self.db, exam_b, 11, _session_data())
        self.assertIsNotNone(session.session_id)
        self.assertTrue(any("already scheduled" in w for w in warnings))

    def test_venue_overlap_is_blocking(self):
        self._publish_with_session()
        exam_b = self._exam(name="Unit 1")
        with self.assertRaises(ConflictError) as ctx:
            repo.create_session(self.db, exam_b, 11, _session_data(
                class_id=2, subject_id=5,
                date=date(2026, 12, 15), start_time=time(10, 0), end_time=time(13, 0)))
        self.assertIn("Venue", str(ctx.exception))

    def test_session_outside_period_is_blocking(self):
        exam = self._exam()
        with self.assertRaises(ConflictError) as ctx:
            repo.create_session(self.db, exam, 11, _session_data(date=date(2027, 3, 1)))
        self.assertIn("period", str(ctx.exception))

    def test_holiday_date_is_warning(self):
        exam = self._exam()
        _, warnings = repo.create_session(
            self.db, exam, 11, _session_data(date=date(2026, 11, 5)))
        self.assertTrue(any("Diwali" in w for w in warnings))

    def test_cross_school_class_and_subject_rejected(self):
        exam = self._exam()
        with self.assertRaises(NotFoundError):
            repo.create_session(self.db, exam, 11, _session_data(class_id=9))
        with self.assertRaises(NotFoundError):
            repo.create_session(self.db, exam, 11, _session_data(subject_id=7))

    def test_published_amendment_versions_and_reschedules(self):
        exam, session = self._publish_with_session()
        # Reason is mandatory once published.
        with self.assertRaises(AppError):
            repo.update_session(self.db, exam, session, 11, {"venue": "Hall B"}, None)
        session, warnings = repo.update_session(
            self.db, exam, session, 11, {"venue": "Hall B"}, "Hall A flooded")
        self.assertEqual(session.status, "rescheduled")
        versions = repo.list_session_versions(self.db, session.session_id)
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].old_row["venue"], "Hall A")
        self.assertEqual(versions[0].new_row["venue"], "Hall B")
        self.assertEqual(versions[0].reason, "Hall A flooded")
        # Draft edits leave no version rows.
        draft = self._exam(name="Unit 1")
        dsess, _ = repo.create_session(
            self.db, draft, 11, _session_data(date=date(2026, 12, 16)))
        repo.update_session(self.db, draft, dsess, 11, {"venue": "Hall C"}, None)
        self.assertEqual(repo.list_session_versions(self.db, dsess.session_id), [])

    def test_cancel_preserves_rows_with_status(self):
        exam, session = self._publish_with_session()
        repo.cancel_examination(self.db, exam, 11, "Floods")
        self.assertEqual(self.db.get(Examination, exam.exam_id).status, "cancelled")
        kept = self.db.get(ExaminationSession, session.session_id)
        self.assertIsNotNone(kept)
        self.assertEqual(kept.status, "cancelled")
        self.assertEqual(len(repo.list_session_versions(self.db, session.session_id)), 1)

    def test_complete_and_cancelled_guards(self):
        exam, session = self._publish_with_session()
        session, _ = repo.complete_session(self.db, exam, session, 11)
        self.assertEqual(session.status, "completed")
        repo.cancel_examination(self.db, exam, 11, "Floods")
        with self.assertRaises(ConflictError):
            repo.complete_session(
                self.db, exam, self.db.get(ExaminationSession, session.session_id), 11)

    def test_timetable_lists_named_ordered_rows(self):
        exam, _ = self._publish_with_session()
        rows = repo.timetable(self.db, 1, year_id=1)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(
            (row["exam_name"], row["exam_type_name"], row["class_name"], row["subject_name"]),
            ("Half-Yearly", "Half-Yearly", "8-A", "Maths"),
        )
        self.assertEqual(repo.timetable(self.db, 2, year_id=1), [])

    def test_cross_school_exam_and_session_are_notfound(self):
        router, _ = _load_academics()
        exam = self._exam()
        with self.assertRaises(NotFoundError):
            router.get_examination(exam.exam_id, self.db, 2, self.admin1)
        session, _ = repo.create_session(self.db, exam, 11, _session_data())
        with self.assertRaises(NotFoundError):
            router.update_examination_session(
                session.session_id, {"venue": "X"}, self.db, 2, self.admin1)


if __name__ == "__main__":
    unittest.main()
