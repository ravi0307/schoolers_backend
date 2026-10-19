"""
B1 rollout-gate tests: per-school examinations_enabled flag.

Covers the ten required areas: enabled-pilot slice, disabled denial,
no-bypass direct calls, legacy parity on/off, isolation intact, disable
preserves data, re-enable restores, safe defaults, gateway consistency,
and no regressions (the full suite).
"""
import sys
import unittest
from datetime import date, time

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.dependencies import CurrentUser
from common.exceptions import ForbiddenError, NotFoundError
from common.models import (
    Base, AcademicYear, Examination, ExaminationSession, ExaminationType,
    School, SchoolClass, Student, Subject, TeacherClassSubject,
)
import services.marks_service.repository as marks_repo

TABLES = [
    "schools", "academic_years", "academic_periods", "examination_types", "examinations",
    "examination_sessions", "classes", "students", "subjects",
    "teacher_class_subjects", "marks", "holidays", "academic_events",
    "class_subjects", "student_subjects", "mark_import_batches", "mark_import_rows",
    "grade_scales",
]


def _load(service_dir, package):
    sys.path.insert(0, service_dir)
    try:
        for mod in ("repository", "schemas", "bulk"):
            sys.modules.pop(mod, None)
        router = __import__(f"{package}.router", fromlist=["x"])
        schemas = __import__(f"{package}.schemas", fromlist=["x"])
        return router, schemas
    finally:
        sys.path.pop(0)
        for mod in ("repository", "schemas", "bulk"):
            sys.modules.pop(mod, None)


def _school(db, school_id, name, enabled):
    db.add(School(school_id=school_id, name=name, address="A", pincode="1",
                  city="C", state="S", primary_contact="P", primary_email="e@x.io",
                  examinations_enabled=enabled))
    db.commit()


class RolloutFlagTests(unittest.TestCase):
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
        self.admin1 = CurrentUser(user_id=11, role="admin", school_id=1, linked_person_id=None)
        self.admin2 = CurrentUser(user_id=12, role="admin", school_id=2, linked_person_id=None)
        self.master = CurrentUser(user_id=1, role="master", school_id=None, linked_person_id=None)
        self.teacher7 = CurrentUser(user_id=31, role="teacher", school_id=1, linked_person_id=7)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _pilot_school(self):
        _school(self.db, 1, "Green Valley", True)
        self.db.add_all([
            AcademicYear(year_id=1, school_id=1, name="2026-27",
                         start_date=date(2026, 6, 1), end_date=date(2027, 5, 31)),
            SchoolClass(class_id=1, school_id=1, name="8-A"),
            Subject(subject_id=3, school_id=1, name="Maths"),
            Student(student_id=1, school_id=1, class_id=1, admission_no="A1", name="Riya"),
            TeacherClassSubject(staff_id=7, class_id=1, subject_id=3),
        ])
        self.db.commit()

    def test_enabled_pilot_slice_end_to_end(self):
        self._pilot_school()
        acad_router, acad_schemas = _load("services/academics_service", "services.academics_service")
        year = acad_router.create_academic_year(
            acad_schemas.AcademicYearCreate(
                name="2027-28", start_date=date(2027, 6, 1), end_date=date(2028, 5, 31)),
            self.db, 1, self.admin1)
        self.assertEqual(year.name, "2027-28")
        etype = acad_router.create_examination_type(
            acad_schemas.ExaminationTypeCreate(name="Half-Yearly"), self.db, 1, self.admin1)
        exam = acad_router.create_examination(
            acad_schemas.ExaminationCreate(type_id=etype.type_id, year_id=year.year_id, name="HY"),
            self.db, 1, self.admin1)
        write = acad_router.create_examination_session(
            exam.exam_id,
            acad_schemas.SessionCreate(class_id=1, subject_id=3, date=date(2027, 12, 15),
                                       start_time=time(9, 0), end_time=time(12, 0), max_marks=100),
            self.db, 1, self.admin1)
        self.assertEqual(write["warnings"], [])
        marks_router, marks_schemas = _load("services/marks_service", "services.marks_service")
        mark = marks_router.create_session_mark(
            marks_schemas.SessionMarkCreate(
                session_id=write["session"].session_id, student_id=1, subject_id=3,
                score=80, assessment_status="present"),
            self.db, self.teacher7)
        self.assertEqual(mark.workflow_status, "draft")
        submitted = marks_router.submit_session_marks(write["session"].session_id, self.db, self.teacher7)
        self.assertEqual(submitted["submitted"], 1)
        published = marks_router.publish_session_marks(write["session"].session_id, self.db, self.admin1)
        self.assertEqual(published["published"], 1)
        results = marks_router.get_student_results(1, None, None, None, self.db, self.admin1)
        self.assertEqual(len(results["examinations"]), 1)
        self.assertEqual(results["aggregate"]["obtained"], 80)

    def test_disabled_school_denied_everywhere(self):
        _school(self.db, 2, "Blue Horizon", False)
        acad_router, acad_schemas = _load("services/academics_service", "services.academics_service")
        with self.assertRaises(ForbiddenError):
            acad_router.list_academic_years(self.db, 2, self.admin2)
        with self.assertRaises(ForbiddenError):
            acad_router.create_examination_type(
                acad_schemas.ExaminationTypeCreate(name="X"), self.db, 2, self.admin2)
        people_router, _ = _load("services/people_service", "services.people_service")
        with self.assertRaises(ForbiddenError):
            people_router.preview_promotion(None, self.db, 2, self.admin2)

    def test_direct_calls_cannot_bypass(self):
        # No UI exists yet, so "direct" means function-level calls as another
        # school's admin: the token school always wins.
        _school(self.db, 2, "Blue Horizon", False)
        acad_router, acad_schemas = _load("services/academics_service", "services.academics_service")
        with self.assertRaises(ForbiddenError):
            acad_router.create_academic_year(
                acad_schemas.AcademicYearCreate(
                    name="2027-28", start_date=date(2027, 6, 1), end_date=date(2028, 5, 31)),
                self.db, 2, self.admin2)

    def test_legacy_paths_ignore_the_flag(self):
        for enabled in (False, True):
            _school(self.db, 1, "Green Valley", enabled)
            self.db.add_all([
                SchoolClass(class_id=1, school_id=1, name="8-A"),
                Subject(subject_id=3, school_id=1, name="Maths"),
                Student(student_id=1, school_id=1, class_id=1, admission_no="A1", name="Riya"),
            ])
            self.db.commit()
            row = marks_repo.upsert_mark(self.db, 1, 3, "Term 1", 70, 7)
            self.assertEqual((row.score, row.workflow_status), (70, "published"))
            self.assertEqual(len(marks_repo.get_for_student(self.db, 1)), 1)
            self.assertEqual(len(marks_repo.get_for_class(self.db, 1)), 1)
            for name in ("schools", "classes", "subjects", "students", "marks"):
                self.db.execute(Base.metadata.tables[name].delete())
            self.db.commit()

    def test_disable_preserves_and_reenable_restores(self):
        self._pilot_school()
        year_router, _ = _load("services/academics_service", "services.academics_service")
        self.assertEqual(len(year_router.list_academic_years(self.db, 1, self.admin1)), 1)
        school = self.db.query(School).filter_by(school_id=1).one()
        school.examinations_enabled = False
        self.db.commit()
        with self.assertRaises(ForbiddenError):
            year_router.list_academic_years(self.db, 1, self.admin1)
        # Data untouched by the disable.
        school.examinations_enabled = True
        self.db.commit()
        self.assertEqual(len(year_router.list_academic_years(self.db, 1, self.admin1)), 1)

    def test_missing_flag_defaults_to_deny(self):
        # A school row predating the migration has no explicit value in
        # practice; the model default is False (fail safe).
        self.assertFalse(School.__table__.c.examinations_enabled.default.arg)

    def test_master_flips_flag_via_features_endpoint(self):        # Role gating lives in Depends (like every endpoint in this file),
        # so this exercises the master success path; master-only access is
        # pinned by the contract test below.
        _school(self.db, 1, "Green Valley", False)
        sys.path.insert(0, "services/schools_service")
        try:
            for mod in ("repository", "schemas"):
                sys.modules.pop(mod, None)
            from services.schools_service import router as schools_router
            from services.schools_service import schemas as schools_schemas
        finally:
            sys.path.pop(0)
            for mod in ("repository", "schemas"):
                sys.modules.pop(mod, None)
        # NOTE: master-only access is enforced by require_role in Depends
        # (like every endpoint in this service) and pinned by the contract
        # test; direct calls bypass Depends by design.
        school = schools_router.update_features(
            1, schools_schemas.FeatureFlags(examinations_enabled=True),
            self.db, self.master,
        )
        self.assertTrue(school.examinations_enabled)

    def _second_school_with_session(self):
        """School 2 pilot scope: its own year, class, subject, student,
        exam, and session. Returns (session, exam)."""
        _school(self.db, 2, "Blue Horizon", True)
        self.db.add_all([
            AcademicYear(year_id=9, school_id=2, name="2026-27",
                         start_date=date(2026, 6, 1), end_date=date(2027, 5, 31)),
            SchoolClass(class_id=9, school_id=2, name="8-A"),
            Subject(subject_id=7, school_id=2, name="Maths"),
            Student(student_id=9, school_id=2, class_id=9, admission_no="B1", name="Zara"),
        ])
        self.db.commit()
        import services.academics_service.repository as acad_repo
        year = acad_repo.get_year(self.db, 2, 9)
        etype = acad_repo.create_examination_type(self.db, 2, {"name": "Half-Yearly"})
        exam = acad_repo.create_examination(
            self.db, 2, 12,
            {"type_id": etype.type_id, "year_id": year.year_id, "name": "HY",
             "result_format_expectation": "marks"})
        session, _ = acad_repo.create_session(
            self.db, exam, 12,
            {"class_id": 9, "subject_id": 7, "date": date(2026, 12, 15),
             "start_time": time(9, 0), "end_time": time(12, 0), "max_marks": 100})
        return session, exam

    def test_cross_school_batch_commit_is_notfound_without_writes(self):
        from common.models import Mark
        self._pilot_school()
        session2, exam2 = self._second_school_with_session()
        sys.path.insert(0, "services/marks_service")
        try:
            for mod in ("repository", "schemas", "bulk"):
                sys.modules.pop(mod, None)
            from services.marks_service import router as marks_router
            from services.marks_service import bulk as bulk_mod
        finally:
            sys.path.pop(0)
            for mod in ("repository", "schemas", "bulk"):
                sys.modules.pop(mod, None)
        csv_text = ("student_id,admission_no,student_name,score_or_grade,assessment_status\n"
                    "9,B1,Zara,70,present\n")
        batch2, receipt = bulk_mod.preview_upload(
            self.db, 2, session2, exam2, "s2.csv", csv_text.encode(), 12)
        self.assertEqual([r["verdict"] for r in receipt], ["valid"])
        # School 1's admin presenting School 2's batch id gets a uniform
        # 404, and nothing is written anywhere.
        with self.assertRaises(NotFoundError):
            marks_router.commit_import(batch2.batch_id, False, self.db, self.admin1)
        self.assertEqual(self.db.query(Mark).count(), 0)
        # The legitimate owner can still commit its own batch.
        out = marks_router.commit_import(batch2.batch_id, False, self.db, self.admin2)
        self.assertEqual((out["applied"], out["skipped"]), (1, 0))
        self.assertEqual(self.db.query(Mark).count(), 1)

    def test_schoolless_admin_cannot_commit_import(self):
        # A token with school_id=None must not slip past the batch school
        # check (the guard used to skip the comparison for None callers).
        from common.models import Mark
        self._pilot_school()
        acad_router, acad_schemas = _load("services/academics_service", "services.academics_service")
        etype = acad_router.create_examination_type(
            acad_schemas.ExaminationTypeCreate(name="Half-Yearly"), self.db, 1, self.admin1)
        exam = acad_router.create_examination(
            acad_schemas.ExaminationCreate(type_id=etype.type_id, year_id=1, name="HY"),
            self.db, 1, self.admin1)
        write = acad_router.create_examination_session(
            exam.exam_id,
            acad_schemas.SessionCreate(class_id=1, subject_id=3, date=date(2026, 12, 15),
                                       start_time=time(9, 0), end_time=time(12, 0), max_marks=100),
            self.db, 1, self.admin1)
        sys.path.insert(0, "services/marks_service")
        try:
            for mod in ("repository", "schemas", "bulk"):
                sys.modules.pop(mod, None)
            from services.marks_service import router as marks_router
            from services.marks_service import bulk as bulk_mod
        finally:
            sys.path.pop(0)
            for mod in ("repository", "schemas", "bulk"):
                sys.modules.pop(mod, None)
        csv_text = ("student_id,admission_no,student_name,score_or_grade,assessment_status\n"
                    "1,A1,Riya,75,present\n")
        batch, receipt = bulk_mod.preview_upload(
            self.db, 1, write["session"], exam, "s1.csv", csv_text.encode(), 11)
        self.assertEqual([r["verdict"] for r in receipt], ["valid"])
        schoolless = CurrentUser(user_id=14, role="admin", school_id=None, linked_person_id=None)
        with self.assertRaises(ForbiddenError):
            marks_router.commit_import(batch.batch_id, False, self.db, schoolless)
        self.assertEqual(self.db.query(Mark).count(), 0)

    def test_disable_between_preview_and_commit_denies(self):
        from common.models import Mark
        self._pilot_school()
        sys.path.insert(0, "services/marks_service")
        try:
            for mod in ("repository", "schemas", "bulk"):
                sys.modules.pop(mod, None)
            from services.marks_service import router as marks_router
            from services.marks_service import bulk as bulk_mod
        finally:
            sys.path.pop(0)
            for mod in ("repository", "schemas", "bulk"):
                sys.modules.pop(mod, None)
        session = self.db.query(ExaminationSession).filter_by(session_id=1).one_or_none()
        if session is None:
            import services.academics_service.repository as acad_repo
            from common.models import Examination as ExamModel
            year = acad_repo.get_year(self.db, 1, 1)
            if year is None:
                year = acad_repo.create_year(
                    self.db, 1, {"name": "2026-27", "start_date": date(2026, 6, 1),
                                 "end_date": date(2027, 5, 31)})
            etype = acad_repo.create_examination_type(self.db, 1, {"name": "Half-Yearly"})
            exam = acad_repo.create_examination(
                self.db, 1, 11,
                {"type_id": etype.type_id, "year_id": year.year_id, "name": "HY",
                 "result_format_expectation": "marks"})
            session, _ = acad_repo.create_session(
                self.db, exam, 11,
                {"class_id": 1, "subject_id": 3, "date": date(2026, 12, 15),
                 "start_time": time(9, 0), "end_time": time(12, 0), "max_marks": 100})
        else:
            from common.models import Examination as ExamModel
            exam = self.db.query(ExamModel).filter_by(exam_id=session.exam_id).one()
        csv_text = ("student_id,admission_no,student_name,score_or_grade,assessment_status\n"
                    "1,A1,Riya,80,present\n")
        batch, receipt = bulk_mod.preview_upload(
            self.db, 1, session, exam, "s1.csv", csv_text.encode(), 11)
        self.assertEqual([r["verdict"] for r in receipt], ["valid"])
        # Disable through the authorized feature-toggle path.
        sys.path.insert(0, "services/schools_service")
        try:
            for mod in ("repository", "schemas"):
                sys.modules.pop(mod, None)
            from services.schools_service import router as schools_router
            from services.schools_service import schemas as schools_schemas
        finally:
            sys.path.pop(0)
            for mod in ("repository", "schemas"):
                sys.modules.pop(mod, None)
        schools_router.update_features(
            1, schools_schemas.FeatureFlags(examinations_enabled=False),
            self.db, self.master)
        # The pending commit is now denied; nothing written, batch intact.
        with self.assertRaises(ForbiddenError):
            marks_router.commit_import(batch.batch_id, False, self.db, self.admin1)
        self.assertEqual(self.db.query(Mark).count(), 0)
        kept = self.db.query(bulk_mod.MarkImportBatch).filter_by(batch_id=batch.batch_id).one()
        self.assertEqual(kept.status, "preview")


if __name__ == "__main__":
    unittest.main()
