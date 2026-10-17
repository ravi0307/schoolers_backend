"""
Functional tests for promotion and subject enrollment (Phase 5).

Covers founding-year reconciliation, preview/commit with idempotent retry,
transactional cache updates, history preservation, subject offerings, and
the enrollment gate on marks entry. Uses sqlite + SQLAlchemy like the rest.
"""
import sys
import unittest
from datetime import date, time
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.dependencies import CurrentUser
from common.exceptions import AppError, ConflictError, NotFoundError
from common.models import (
    Base, AcademicYear, ClassEnrollment, PromotionBatch, School, SchoolClass,
    Student, Subject,
)
import services.people_service.repository as repo

TABLES = [
    "schools", "academic_years", "classes", "students", "subjects", "class_enrollments",
    "promotion_batches", "class_subjects", "student_subjects",
    "examination_types", "examinations", "examination_sessions", "marks",
]


def _load_people_router():
    sys.path.insert(0, "services/people_service")
    try:
        for mod in ("repository", "schemas"):
            sys.modules.pop(mod, None)
        from services.people_service import router as people_router
        from services.people_service import schemas as people_schemas
        return people_router, people_schemas
    finally:
        sys.path.pop(0)
        for mod in ("repository", "schemas"):
            sys.modules.pop(mod, None)


def _year(db, year_id, school_id, name, start, end):
    db.add(AcademicYear(year_id=year_id, school_id=school_id, name=name,
                        start_date=start, end_date=end))
    db.commit()


class PromotionTests(unittest.TestCase):
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
        _year(self.db, 1, 1, "2026-27", date(2026, 6, 1), date(2027, 5, 31))
        _year(self.db, 2, 1, "2027-28", date(2027, 6, 1), date(2028, 5, 31))
        self.db.add(
            School(school_id=1, name="Green Valley", address="A", pincode="1",
                   city="C", state="S", primary_contact="P", primary_email="e@x.io",
                   examinations_enabled=True))
        self.db.add_all([
            SchoolClass(class_id=1, school_id=1, name="8-A"),
            SchoolClass(class_id=2, school_id=1, name="8-B"),
            SchoolClass(class_id=3, school_id=1, name="9-A"),
            Student(student_id=1, school_id=1, class_id=1, admission_no="A1", name="Riya"),
            Student(student_id=2, school_id=1, class_id=1, admission_no="A2", name="Dev"),
            Student(student_id=3, school_id=1, class_id=2, admission_no="A3", name="Una"),
        ])
        self.db.commit()
        self.admin1 = CurrentUser(user_id=11, role="admin", school_id=1, linked_person_id=None)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_founding_preview_proposes_current_classes(self):
        out = repo.preview_promotion(self.db, 1, None, 1, {})
        self.assertEqual(len(out["proposed"]), 3)
        self.assertTrue(all(r["suggested_status"] == "enrolled" for r in out["proposed"]))
        self.assertEqual(out["exceptions"], [])
        # Nothing written by a preview.
        self.assertEqual(self.db.query(ClassEnrollment).count(), 0)

    def test_mapped_preview_suggests_promoted_and_retained(self):
        out = repo.preview_promotion(self.db, 1, 1, 2, {1: 3, 2: 2})
        by_student = {r["student_id"]: r for r in out["proposed"]}
        self.assertEqual(by_student[1]["suggested_status"], "promoted")
        self.assertEqual(by_student[1]["to_class_id"], 3)
        self.assertEqual(by_student[3]["suggested_status"], "retained")

    def test_unmapped_class_is_an_exception(self):
        out = repo.preview_promotion(self.db, 1, 1, 2, {1: 3})
        self.assertEqual(sorted(r["student_id"] for r in out["proposed"]), [1, 2])
        self.assertEqual(len(out["exceptions"]), 1)

    def test_commit_applies_cache_and_history_atomically(self):
        # Founding reconciliation first: year 1 holds the current classes.
        repo.commit_promotion(
            self.db, 1, 11, None, 1,
            [{"student_id": 1, "to_class_id": 1, "status": "enrolled"},
             {"student_id": 2, "to_class_id": 1, "status": "enrolled"},
             {"student_id": 3, "to_class_id": 2, "status": "enrolled"}],
            "founding",
        )
        out = repo.commit_promotion(
            self.db, 1, 11, 1, 2,
            [{"student_id": 1, "to_class_id": 3, "status": "promoted"},
             {"student_id": 3, "to_class_id": 2, "status": "retained"}],
            "key-1",
        )
        self.assertEqual(out["applied"], 2)
        self.assertFalse(out["duplicate_receipt"])
        self.assertEqual(
            self.db.query(Student).filter_by(student_id=1).one().class_id, 3)
        # Prior-year view still resolves the old class via enrollment.
        prior = repo.students_in_class_year(self.db, 1, 1)
        self.assertEqual(sorted(s.student_id for s in prior), [1, 2])

    def test_commit_is_idempotent_by_key(self):
        args = (self.db, 1, 11, 1, 2,
                [{"student_id": 1, "to_class_id": 3, "status": "promoted"}], "key-9")
        first = repo.commit_promotion(*args)
        second = repo.commit_promotion(*args)
        self.assertTrue(second["duplicate_receipt"])
        self.assertEqual(first["batch_id"], second["batch_id"])
        self.assertEqual(self.db.query(ClassEnrollment).count(), 1)

    def test_commit_rejects_duplicates_and_cross_school(self):
        repo.commit_promotion(
            self.db, 1, 11, 1, 2,
            [{"student_id": 1, "to_class_id": 3, "status": "promoted"}], "k1")
        with self.assertRaises(ConflictError):
            repo.commit_promotion(
                self.db, 1, 11, 1, 2,
                [{"student_id": 1, "to_class_id": 3, "status": "promoted"}], "k2")
        with self.assertRaises(NotFoundError):
            repo.commit_promotion(
                self.db, 1, 11, 1, 2,
                [{"student_id": 4242, "to_class_id": 3, "status": "promoted"}], "k3")
        with self.assertRaises(AppError):
            repo.commit_promotion(
                self.db, 1, 11, 1, 2,
                [{"student_id": 2, "to_class_id": 3, "status": "withdrawn"}], "k4")

    def test_transferred_and_new_statuses(self):
        out = repo.commit_promotion(
            self.db, 1, 11, 1, 2,
            [{"student_id": 1, "status": "transferred"},
             {"student_id": 2, "to_class_id": 3, "status": "new"}],
            "k-transfer",
        )
        self.assertEqual(out["applied"], 2)
        # Transferred student keeps the old cache (no class to move to).
        self.assertEqual(
            self.db.query(Student).filter_by(student_id=1).one().class_id, 1)
        self.assertEqual(
            self.db.query(Student).filter_by(student_id=2).one().class_id, 3)

    def test_router_preview_and_commit_are_admin_scoped(self):
        router, schemas = _load_people_router()
        out = router.preview_promotion(
            schemas.PromotionPreview(source_year_id=None, target_year_id=1, class_map=[]),
            self.db, 1, self.admin1,
        )
        self.assertEqual(len(out["proposed"]), 3)
        committed = router.commit_promotion(
            schemas.PromotionCommit(
                source_year_id=None, target_year_id=1,
                assignments=[schemas.PromotionAssignment(
                    student_id=1, to_class_id=1, status="enrolled")],
                idempotency_key="router-k",
            ),
            self.db, 1, self.admin1,
        )
        self.assertEqual(committed["applied"], 1)
        with self.assertRaises(NotFoundError):
            router.commit_promotion(
                schemas.PromotionCommit(
                    source_year_id=None, target_year_id=4242, assignments=[],
                    idempotency_key="router-k2"),
                self.db, 1, self.admin1,
            )


class OfferingEnrollmentTests(unittest.TestCase):
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
        _year(self.db, 1, 1, "2026-27", date(2026, 6, 1), date(2027, 5, 31))
        self.db.add_all([
            SchoolClass(class_id=1, school_id=1, name="8-A"),
            Subject(subject_id=3, school_id=1, name="Maths"),
            Subject(subject_id=5, school_id=1, name="Science"),
            Student(student_id=1, school_id=1, class_id=1, admission_no="A1", name="Riya"),
            Student(student_id=2, school_id=1, class_id=1, admission_no="A2", name="Dev"),
        ])
        self.db.commit()
        self.admin1 = CurrentUser(user_id=11, role="admin", school_id=1, linked_person_id=None)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_offerings_gate_session_subjects(self):
        import services.academics_service.repository as acad_repo
        # No offerings yet: legacy behavior, everything passes.
        self.assertTrue(acad_repo.subject_offered(self.db, 1, 3))
        acad_repo.set_offering(self.db, 1, 3, True)
        self.assertTrue(acad_repo.subject_offered(self.db, 1, 3))
        self.assertFalse(acad_repo.subject_offered(self.db, 1, 5))
        listed = acad_repo.list_offerings(self.db, 1)
        self.assertEqual([(r["subject_id"], r["is_required"]) for r in listed], [(3, True)])

    def test_enrollment_gate_is_opt_in_per_student(self):
        import services.marks_service.repository as marks_repo
        # No rows for the student: passes (school hasn't adopted enrollment).
        self.assertTrue(marks_repo.student_enrolled_for_session(self.db, 1, 3, 1))
        acad_repo = __import__("services.academics_service.repository", fromlist=["x"])
        acad_repo.enroll_student_subject(self.db, 1, 3, 1)
        acad_repo.enroll_student_subject(self.db, 1, 5, 1)
        drop = [r for r in acad_repo.list_student_subjects(self.db, 1, 1) if r.subject_id == 5][0]
        drop.state = "dropped"
        self.db.commit()
        self.assertTrue(marks_repo.student_enrolled_for_session(self.db, 1, 3, 1))
        self.assertFalse(marks_repo.student_enrolled_for_session(self.db, 1, 5, 1))
        # Another student with no rows is unaffected.
        self.assertTrue(marks_repo.student_enrolled_for_session(self.db, 2, 5, 1))

    def test_offering_delete_blocked_with_live_sessions(self):
        import services.academics_service.repository as acad_repo
        from common.models import Examination, ExaminationSession, ExaminationType
        self.db.add(ExaminationType(type_id=1, school_id=1, name="Half-Yearly", default_max_marks=100))
        self.db.add(Examination(exam_id=1, school_id=1, type_id=1, year_id=1,
                                name="Half-Yearly", result_format_expectation="marks",
                                status="draft"))
        self.db.add(ExaminationSession(
            session_id=1, exam_id=1, class_id=1, subject_id=3,
            date=date(2026, 12, 15), start_time=time(9, 0),
            end_time=time(12, 0), max_marks=100, status="scheduled"))
        self.db.commit()
        acad_repo.set_offering(self.db, 1, 3, True)
        with self.assertRaises(ConflictError):
            acad_repo.delete_offering(self.db, 1, 3)


if __name__ == "__main__":
    unittest.main()
